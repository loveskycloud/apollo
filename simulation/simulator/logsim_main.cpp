/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include "gflags/gflags.h"

#include "cyber/common/file.h"
#include "cyber/init.h"
#include "google/protobuf/text_format.h"

#include "simulation/logsim/proto/simulation_task.pb.h"
#include "simulation/simulator/dag_controller.h"
#include "simulation/simulator/ego_car.h"
#include "simulation/simulator/module_catalog.h"
#include "simulation/simulator/proto/scenario.pb.h"
#include "simulation/simulator/scenario_util.h"
#include "simulation/simulator/sim_initializer.h"

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
  if (LoadScenarioFile(scenario_path, &scenario) &&
      scenario.enabled_modules_size() > 0) {
    return scenario;
  }

  std::vector<std::string> modules;
  for (const auto& m : task.runtime_modules()) {
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

int main(int argc, char** argv) {
  google::ParseCommandLineFlags(&argc, &argv, true);
  if (FLAGS_task_dir.empty()) {
    std::cerr << "Usage: logsim_main --task_dir=/path/to/task\n";
    return 1;
  }

  apollo::simulation::logsim::SimulationTask task;
  if (!LoadTask(FLAGS_task_dir, &task)) {
    std::cerr << "failed to load task.pb.txt under " << FLAGS_task_dir << "\n";
    return 1;
  }
  task.set_task_dir(FLAGS_task_dir);

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
  if (!initializer.Init(FLAGS_task_dir, &ctx)) {
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

  const int code = initializer.Run(&ctx);
  const std::string bag_path = ctx.result_sink.path();
  const uint64_t bag_msgs = ctx.result_sink.written_count();
  cleanup();

  std::cout << "sim bag: " << bag_path << " messages=" << bag_msgs << "\n";
  std::cout << "logsim_main finished, exit=" << code << "\n";
  return code;
}
