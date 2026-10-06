/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include <iostream>
#include <cstdlib>
#include <stdexcept>
#include <memory>
#include <string>
#include <vector>
#include <signal.h>
#include <sys/prctl.h>
#include <unistd.h>

#include "gflags/gflags.h"

#include "cyber/common/file.h"
#include "cyber/init.h"
#include "cyber/timer/sim_timer_registry.h"
#include "google/protobuf/text_format.h"

#include "modules/simulation/logsim/proto/simulation_task.pb.h"
#include "modules/simulation/simulator/dag_controller.h"
#include "modules/simulation/simulator/ego_car.h"
#include "modules/simulation/simulator/module_catalog.h"
#include "modules/simulation/simulator/proto/scenario.pb.h"
#include "modules/simulation/simulator/scenario_util.h"
#include "modules/simulation/simulator/sim_initializer.h"
#include "modules/simulation/simulator/sim_runner.h"

DEFINE_string(task_dir, "", "Simulation task directory");
DEFINE_bool(skip_modules, false,
            "Skip DagController module load (smoke without PnC .so)");
DEFINE_bool(skip_ego_env, false,
            "Skip EgoCar environment init (map/vehicle)");

namespace {

bool LoadTask(const std::string& task_dir,
              apollo::simulation::logsim::SimulationTask* task) {
  const std::string path = task_dir + "/task.pb.txt";
  std::string content;
  if (!apollo::cyber::common::GetContent(path, &content)) {
    return false;
  }
  return google::protobuf::TextFormat::ParseFromString(content, task);
}

bool LoadScenarioFile(const std::string& path,
                      apollo::simulation::simulator::Scenario* scenario) {
  std::string content;
  if (!apollo::cyber::common::GetContent(path, &content)) {
    return false;
  }
  return google::protobuf::TextFormat::ParseFromString(content, scenario);
}

apollo::simulation::simulator::Scenario ResolveScenario(
    const apollo::simulation::logsim::SimulationTask& task) {
  apollo::simulation::simulator::Scenario scenario;
  const std::string scenario_path = task.task_dir() + "/scenario.pb.txt";
  if (apollo::cyber::common::PathExists(scenario_path)) {
    if (!LoadScenarioFile(scenario_path, &scenario) ||
        scenario.enabled_modules_size() == 0) {
      throw std::runtime_error("Invalid scenario module configuration: " + scenario_path);
    }
    return scenario;
  }

  std::vector<std::string> modules;
  for (const auto& m : task.runtime_modules()) {
    if (apollo::simulation::ModuleCatalog::ParseModuleType(m) ==
        apollo::simulation::simulator::MODULE_UNKNOWN) {
      throw std::runtime_error("Unknown runtime module: " + m);
    }
    modules.push_back(m);
  }
  scenario = apollo::simulation::ScenarioUtil::BuildFromRuntimeModules(
      modules, task.scenario_id());
  if (scenario.map_dir().empty() && !task.map_dir().empty()) {
    scenario.set_map_dir(task.map_dir());
  }
  if (scenario.vehicle().empty() && !task.vehicle_config_path().empty()) {
    scenario.set_vehicle(task.vehicle_config_path());
  }

  for (int i = 0; i < task.dag_paths_size() &&
                  i < static_cast<int>(modules.size());
       ++i) {
    const auto type =
        apollo::simulation::ModuleCatalog::ParseModuleType(modules[i]);
    if (type == apollo::simulation::simulator::MODULE_UNKNOWN) {
      continue;
    }
    auto* spec = scenario.add_module_specs();
    spec->set_type(type);
    spec->set_dag_path(task.dag_paths(i));
  }
  return scenario;
}

}  // namespace

static int RunTask(int argc, char** argv) {
  // Keep fatal native-module diagnostics in the task log, including crashes
  // during static teardown. Cyber Init subsequently installs graceful SIGTERM.
  google::InstallFailureSignalHandler();
  std::cout << std::unitbuf;
  google::ParseCommandLineFlags(&argc, &argv, true);
  if (FLAGS_task_dir.empty()) {
    std::cerr << "Usage: simulator_main --task_dir=/path/to/task\n";
    return 1;
  }
  if (!apollo::cyber::common::PathIsAbsolute(FLAGS_task_dir)) {
    FLAGS_task_dir = apollo::cyber::common::GetCurrentPath() + "/" + FLAGS_task_dir;
  }

  apollo::simulation::logsim::SimulationTask task;
  if (!LoadTask(FLAGS_task_dir, &task)) {
    std::cerr << "failed to load task.pb.txt under " << FLAGS_task_dir << "\n";
    return 1;
  }
  task.set_task_dir(FLAGS_task_dir);
  // Identical installed-module lookup for direct CLI aliases and queued runs.
  // A task overlay contains configs, not copies of Apollo shared libraries.
  const char* distribution_env = std::getenv("APOLLO_DISTRIBUTION_HOME");
  const std::string distribution = distribution_env && distribution_env[0]
      ? distribution_env : "/opt/apollo/neo";
  setenv("APOLLO_LIB_PATH", (distribution + "/lib").c_str(), 1);
  setenv("APOLLO_PLUGIN_LIB_PATH", (distribution + "/lib").c_str(), 1);
  setenv("APOLLO_PLUGIN_DESCRIPTION_PATH", distribution.c_str(), 1);
  setenv("APOLLO_PLUGIN_INDEX_PATH", (distribution + "/share/cyber_plugin_index").c_str(), 1);
  if (!task.profile_path().empty()) {
    const std::string runtime = apollo::cyber::common::PathIsAbsolute(task.profile_path())
        ? task.profile_path() : apollo::cyber::common::GetCurrentPath() + "/" + task.profile_path();
    if (!apollo::cyber::common::DirectoryExists(runtime) || chdir(runtime.c_str()) != 0) {
      throw std::runtime_error("Cannot enter the task's runtime profile: " + runtime);
    }
    for (const char* key : {"APOLLO_RUNTIME_PATH", "APOLLO_ENV_WORKROOT", "APOLLO_DAG_PATH", "APOLLO_FLAG_PATH", "APOLLO_CONF_PATH"}) {
      setenv(key, runtime.c_str(), 1);
    }
  }
  if (task.sim_mode() != apollo::simulation::logsim::DEFAULT ||
      task.enable_onboard_latency()) {
    throw std::runtime_error("Only synchronous DEFAULT scheduling is implemented; latency/aligned/decoupled modes must not silently use DEFAULT");
  }
  std::srand(static_cast<unsigned int>(task.random_seed()));

  const auto scenario = ResolveScenario(task);

  // 1) Ego environment only (map / vehicle / hdmap).
  std::unique_ptr<apollo::simulation::EgoCar> ego_car(
      new apollo::simulation::EgoCar());
  if (!FLAGS_skip_ego_env) {
    if (!ego_car->Init(scenario, task)) {
      std::cerr << "EgoCar environment init failed\n";
      return 1;
    }
  }

  // 2) Cyber + emulator pipeline.
  apollo::simulation::SimInitializer initializer;
  apollo::simulation::SimInitializer::Context ctx;
  if (!initializer.Init(FLAGS_task_dir, scenario, &ctx)) {
    std::cerr << "SimInitializer failed\n";
    return 1;
  }

  // 3) Enabled modules: DagController → ClassLoader → ClassController.
  //    Pass EgoCar so module flagfiles override map_dir/vehicle last.
  std::unique_ptr<apollo::simulation::DagController> dag_controller(
      new apollo::simulation::DagController());
  const std::string flag_out_dir = FLAGS_task_dir + "/sim_flags";
  auto cleanup = [&]() {
    // Destroy modules + unload .so BEFORE cyber::Clear / process exit.
    // Leaving DagController alive until after Clear() caused segfaults
    // (class_loader still holding ComponentBase refs during static teardown).
    if (dag_controller) {
      dag_controller->Shutdown();
      apollo::cyber::SimTimerRegistry::Instance()->Clear();
      dag_controller.reset();
    }
    ctx.output_recorder.Stop();
    ctx.result_sink.Flush();
    ctx.node.reset();
    apollo::cyber::Clear();
  };

  if (!FLAGS_skip_modules) {
    apollo::simulation::EgoCar* ego_ptr =
        (!FLAGS_skip_ego_env && ego_car->ready()) ? ego_car.get() : nullptr;
    if (!dag_controller->Start(scenario, ego_ptr, flag_out_dir)) {
      std::cerr << "DagController failed to load enabled modules\n";
      cleanup();
      return 1;
    }
    std::cout << "module_list size=" << dag_controller->module_count()
              << "\n";
    for (const auto& inst : dag_controller->module_list()) {
      std::cout << "  - "
                << apollo::simulation::ModuleCatalog::ModuleTypeName(inst.type)
                << " class=" << inst.class_name
                << " name=" << inst.module_name << "\n";
    }
  }

  int code = initializer.Run(&ctx);
  if (!FLAGS_skip_modules && apollo::simulation::ScenarioUtil::IsEnabled(
          scenario, apollo::simulation::simulator::LOCALIZATION) &&
      ctx.result_sink.written_count("/apollo/localization/pose") == 0) {
    std::cerr << "LOCALIZATION produced no pose; check GNSS/IMU/INS status and static TF\n";
    code = 1;
  }
  const std::string bag_path = ctx.result_sink.path();
  if (!FLAGS_skip_modules && apollo::simulation::ScenarioUtil::IsEnabled(
          scenario, apollo::simulation::simulator::PERCEPTION) &&
      ctx.result_sink.written_count("/apollo/perception/obstacles") == 0) {
    std::cerr << "PERCEPTION produced no obstacles message; check pointcloud channel, TF, models and GPU\n";
    code = 1;
  }
  const uint64_t bag_msgs = ctx.result_sink.written_count();
  cleanup();

  std::cout << "sim bag: " << bag_path << " messages=" << bag_msgs << "\n";
  std::cout << "simulator_main finished, exit=" << code << "\n";
  return code;
}

int RunSimulator(int argc, char** argv) {
  try {
    // Queue-owned jobs must not survive a crashed/killed supervisor. Set this in
    // the child itself (not an unsafe preexec_fn in the multithreaded worker).
    if (const char* parent = std::getenv("SIM_PARENT_PID")) {
      if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0 ||
          getppid() != std::stol(parent)) {
        throw std::runtime_error("Simulation supervisor is no longer available");
      }
    }
    return RunTask(argc, argv);
  } catch (const std::exception& e) {
    std::cerr << "Simulation failed: " << e.what() << "\n";
    return 1;
  }
}
