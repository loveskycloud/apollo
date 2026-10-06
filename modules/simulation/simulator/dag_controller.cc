/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/dag_controller.h"

#include "cyber/common/file.h"
#include "cyber/common/global_data.h"
#include "cyber/common/log.h"
#include "cyber/plugin_manager/plugin_manager.h"
#include "cyber/scheduler/scheduler_factory.h"

namespace apollo {
namespace simulation {

DagController::DagController() : class_controller_(&class_loader_) {}

DagController::~DagController() { Shutdown(); }

bool DagController::LoadPlugins() {
  // Same as cyber mainboard ModuleController::LoadAll — Prediction/Control
  // model plugins (SemanticLstm*, MultiAgent*, lat/lon controllers, ...) are
  // discovered via cyber_plugin_index, not the module DAG .so alone.
  auto* pm = cyber::plugin_manager::PluginManager::Instance();
  if (!pm->LoadInstalledPlugins()) {
    AWARN << "DagController: LoadInstalledPlugins returned false";
  }
  AINFO << "DagController: installed plugins loaded";
  return true;
}

bool DagController::Start(const simulator::Scenario& scenario, EgoCar* ego,
                          const std::string& flag_out_dir) {
  if (started_) {
    AWARN << "DagController already started";
    return true;
  }
  scenario_ = scenario;
  ego_ = ego;
  flag_out_dir_ = flag_out_dir;
  if (scenario_.enabled_modules_size() == 0) {
    AERROR << "DagController::Start: no enabled_modules in scenario";
    return false;
  }

  if (!LoadPlugins()) {
    AWARN << "DagController: continue without full plugin set";
  }

  if (ENABLE(LOCALIZATION)) {
    if (!LoadModule(simulator::LOCALIZATION)) {
      return false;
    }
  }
  if (ENABLE(PERCEPTION)) {
    if (!LoadModule(simulator::PERCEPTION)) {
      return false;
    }
  }
  if (ENABLE(PREDICTION)) {
    if (!LoadModule(simulator::PREDICTION)) {
      return false;
    }
  }
  if (ENABLE(PLANNING)) {
    if (!LoadModule(simulator::PLANNING)) {
      return false;
    }
  }
  if (ENABLE(CONTROL)) {
    if (!LoadModule(simulator::CONTROL)) {
      return false;
    }
  }
  if (ENABLE(ROUTING)) {
    if (!LoadModule(simulator::ROUTING)) {
      return false;
    }
  }

  if (module_count() == 0) {
    AERROR << "DagController: no module instances created";
    Shutdown();
    return false;
  }

  if (has_timer_component_) {
    total_component_nums_ += cyber::scheduler::Instance()->TaskPoolSize();
  }
  cyber::common::GlobalData::Instance()->SetComponentNums(
      total_component_nums_);

  started_ = true;
  AINFO << "DagController started, instances=" << module_count();
  return true;
}

bool DagController::LoadModule(simulator::ModuleType type) {
  const auto spec =
      ModuleCatalog::Instance().ResolveSpec(type, scenario_);
  if (spec.dag_path().empty()) {
    AERROR << "no dag for module "
           << ModuleCatalog::ModuleTypeName(type);
    return false;
  }

  AINFO << "DagController ENABLE("
        << ModuleCatalog::ModuleTypeName(type)
        << ") → LoadDag / LoadLibrary / CreateInit";

  cyber::proto::DagConfig dag_config;
  std::string resolved_dag;
  if (!LoadDag(spec, &dag_config, &resolved_dag)) {
    return false;
  }
  UpdateComponentNums(dag_config);

  if (!InjectEgoFlagOverrides(&dag_config,
                              ModuleCatalog::ModuleTypeName(type))) {
    return false;
  }

  if (dag_config.module_config_size() == 0) {
    return false;
  }
  // A perception DAG contains several libraries. Each component must be
  // instantiated by the library belonging to its own module_config.
  for (const auto& module_config : dag_config.module_config()) {
    cyber::proto::DagConfig component_dag;
    *component_dag.add_module_config() = module_config;
    std::string resolved_lib;
    if (!LoadLibraryForDag(component_dag, spec, &resolved_lib) ||
        !class_controller_.CreateInitAndAppend(
            component_dag, type, spec.name(), resolved_dag, resolved_lib)) {
      return false;
    }
  }
  return ego_ == nullptr || !ego_->ready() || ego_->VerifyEnvironment();
}

bool DagController::InjectEgoFlagOverrides(
    cyber::proto::DagConfig* dag_config, const std::string& module_tag) {
  if (ego_ == nullptr || !ego_->ready() || flag_out_dir_.empty()) {
    return true;
  }
  if (!cyber::common::EnsureDirectory(flag_out_dir_)) {
    AERROR << "DagController: cannot create flag_out_dir=" << flag_out_dir_;
    return false;
  }

  auto rewrite_path = [&](std::string* flag_path, int idx) -> bool {
    if (flag_path->empty()) {
      return true;
    }
    const std::string out = flag_out_dir_ + "/" + module_tag + "_" +
                            std::to_string(idx) + ".flags";
    if (!ego_->WriteOverrideFlagfile(*flag_path, out)) {
      return false;
    }
    *flag_path = out;
    return true;
  };

  int idx = 0;
  for (auto& module_config : *dag_config->mutable_module_config()) {
    for (auto& component : *module_config.mutable_components()) {
      auto* cfg = component.mutable_config();
      std::string path = cfg->flag_file_path();
      if (!rewrite_path(&path, idx++)) {
        return false;
      }
      cfg->set_flag_file_path(path);
    }
    for (auto& component : *module_config.mutable_timer_components()) {
      auto* cfg = component.mutable_config();
      std::string path = cfg->flag_file_path();
      if (!rewrite_path(&path, idx++)) {
        return false;
      }
      cfg->set_flag_file_path(path);
    }
  }
  return true;
}

bool DagController::LoadDag(const simulator::ModuleSpec& spec,
                            cyber::proto::DagConfig* dag_config,
                            std::string* resolved_dag_path) {
  std::string module_path = spec.dag_path();
  if (!cyber::common::GetFilePathWithEnv(spec.dag_path(), "APOLLO_DAG_PATH",
                                         &module_path)) {
    AERROR << "no dag conf [" << spec.dag_path() << "] found";
    return false;
  }
  if (!cyber::common::GetProtoFromFile(module_path, dag_config)) {
    AERROR << "failed to parse dag: " << module_path;
    return false;
  }
  if (resolved_dag_path) {
    *resolved_dag_path = module_path;
  }
  AINFO << "DagController loaded dag: " << module_path;
  return true;
}

bool DagController::LoadLibraryForDag(
    const cyber::proto::DagConfig& dag_config,
    const simulator::ModuleSpec& spec, std::string* resolved_library_path) {
  for (const auto& module_config : dag_config.module_config()) {
    std::string load_path;
    if (!spec.library_path().empty()) {
      load_path = spec.library_path();
      if (!cyber::common::GetFilePathWithEnv(spec.library_path(),
                                             "APOLLO_LIB_PATH", &load_path)) {
        load_path = spec.library_path();
      }
    } else if (!cyber::common::GetFilePathWithEnv(
                   module_config.module_library(), "APOLLO_LIB_PATH",
                   &load_path)) {
      AERROR << "no module library [" << module_config.module_library()
             << "] found";
      return false;
    }
    if (!class_loader_.LoadLibrary(load_path)) {
      return false;
    }
    if (resolved_library_path) {
      *resolved_library_path = load_path;
    }
  }
  return dag_config.module_config_size() > 0;
}

void DagController::UpdateComponentNums(
    const cyber::proto::DagConfig& dag_config) {
  for (const auto& module_config : dag_config.module_config()) {
    total_component_nums_ += module_config.components_size();
    if (module_config.timer_components_size() > 0) {
      has_timer_component_ = true;
    }
  }
}

void DagController::Shutdown() {
  class_controller_.Shutdown();
  // Intentionally skip class_loader_.UnloadAll() here. At process exit,
  // cyber/PluginManager can still hold cross-refs so UnloadLibrary sees
  // classobj_ref_count_ > 0 and dlclose of prediction/planning .so SEGV.
  // Address space is reclaimed on exit anyway.
  started_ = false;
  total_component_nums_ = 0;
  has_timer_component_ = false;
}

}  // namespace simulation
}  // namespace apollo
