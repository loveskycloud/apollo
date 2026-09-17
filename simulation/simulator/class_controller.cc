/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/class_controller.h"

#include <utility>

#include "cyber/common/log.h"

namespace apollo {
namespace simulation {

ClassController::ClassController(SimClassLoader* class_loader)
    : class_loader_(class_loader) {}

bool ClassController::CreateInitAndAppend(
    const cyber::proto::DagConfig& dag_config, simulator::ModuleType type,
    const std::string& module_name, const std::string& dag_path,
    const std::string& library_path) {
  if (class_loader_ == nullptr) {
    AERROR << "ClassController has no SimClassLoader";
    return false;
  }

  for (const auto& module_config : dag_config.module_config()) {
    for (const auto& component : module_config.components()) {
      const std::string& class_name = component.class_name();
      if (!class_loader_->IsClassRegistered(class_name)) {
        AERROR << "constructor not registered: " << class_name << " in "
               << library_path;
        return false;
      }
      auto base = class_loader_->Create(class_name, library_path);
      if (!base || !base->Initialize(component.config())) {
        AERROR << "create/init failed: " << class_name;
        return false;
      }
      ModuleInstance inst;
      inst.type = type;
      inst.module_name = component.config().name().empty()
                             ? module_name
                             : component.config().name();
      inst.class_name = class_name;
      inst.dag_path = dag_path;
      inst.library_path = library_path;
      inst.component = std::move(base);
      module_list_.push_back(std::move(inst));
      AINFO << "ClassController added: class=" << class_name
            << " name=" << module_list_.back().module_name;
    }

    for (const auto& component : module_config.timer_components()) {
      const std::string& class_name = component.class_name();
      if (!class_loader_->IsClassRegistered(class_name)) {
        AERROR << "constructor not registered: " << class_name << " in "
               << library_path;
        return false;
      }
      auto base = class_loader_->Create(class_name, library_path);
      if (!base || !base->Initialize(component.config())) {
        AERROR << "create/init timer failed: " << class_name;
        return false;
      }
      ModuleInstance inst;
      inst.type = type;
      inst.module_name = component.config().name().empty()
                             ? module_name
                             : component.config().name();
      inst.class_name = class_name;
      inst.dag_path = dag_path;
      inst.library_path = library_path;
      inst.component = std::move(base);
      module_list_.push_back(std::move(inst));
      AINFO << "ClassController added timer: class=" << class_name
            << " name=" << module_list_.back().module_name;
    }
  }
  return true;
}

void ClassController::Shutdown() {
  for (auto& inst : module_list_) {
    if (inst.component) {
      inst.component->Shutdown();
    }
  }
  module_list_.clear();
}

bool ClassController::HasModule(simulator::ModuleType type) const {
  for (const auto& inst : module_list_) {
    if (inst.type == type) {
      return true;
    }
  }
  return false;
}

}  // namespace simulation
}  // namespace apollo
