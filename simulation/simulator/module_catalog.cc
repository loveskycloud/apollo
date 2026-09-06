/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/module_catalog.h"

#include "cyber/common/log.h"

namespace apollo {
namespace simulation {

namespace {

void FillDefault(simulator::ModuleSpec* spec, simulator::ModuleType type,
                 const std::string& name, const std::string& dag) {
  spec->set_type(type);
  spec->set_name(name);
  spec->set_dag_path(dag);
}

}  // namespace

ModuleCatalog::ModuleCatalog() {
  simulator::ModuleSpec pred;
  FillDefault(&pred, simulator::PREDICTION, "prediction",
              "modules/prediction/dag/prediction.dag");
  defaults_[static_cast<int>(simulator::PREDICTION)] = pred;

  simulator::ModuleSpec plan;
  FillDefault(&plan, simulator::PLANNING, "planning",
              "modules/planning/planning_component/dag/planning.dag");
  defaults_[static_cast<int>(simulator::PLANNING)] = plan;

  simulator::ModuleSpec ctrl;
  FillDefault(&ctrl, simulator::CONTROL, "control",
              "modules/control/control_component/dag/control.dag");
  defaults_[static_cast<int>(simulator::CONTROL)] = ctrl;

  simulator::ModuleSpec routing;
  FillDefault(&routing, simulator::ROUTING, "routing",
              "modules/routing/dag/routing.dag");
  defaults_[static_cast<int>(simulator::ROUTING)] = routing;
}

const ModuleCatalog& ModuleCatalog::Instance() {
  static ModuleCatalog catalog;
  return catalog;
}

bool ModuleCatalog::Has(simulator::ModuleType type) const {
  return defaults_.count(static_cast<int>(type)) > 0;
}

simulator::ModuleSpec ModuleCatalog::GetDefaultSpec(
    simulator::ModuleType type) const {
  auto it = defaults_.find(static_cast<int>(type));
  if (it == defaults_.end()) {
    return simulator::ModuleSpec();
  }
  return it->second;
}

simulator::ModuleSpec ModuleCatalog::ResolveSpec(
    simulator::ModuleType type, const simulator::Scenario& scenario) const {
  simulator::ModuleSpec spec = GetDefaultSpec(type);
  for (const auto& override_spec : scenario.module_specs()) {
    if (override_spec.type() == type) {
      if (!override_spec.name().empty()) {
        spec.set_name(override_spec.name());
      }
      if (!override_spec.dag_path().empty()) {
        spec.set_dag_path(override_spec.dag_path());
      }
      if (!override_spec.library_path().empty()) {
        spec.set_library_path(override_spec.library_path());
      }
      break;
    }
  }
  return spec;
}

std::string ModuleCatalog::ModuleTypeName(simulator::ModuleType type) {
  switch (type) {
    case simulator::PREDICTION:
      return "PREDICTION";
    case simulator::PLANNING:
      return "PLANNING";
    case simulator::CONTROL:
      return "CONTROL";
    case simulator::ROUTING:
      return "ROUTING";
    default:
      return "MODULE_UNKNOWN";
  }
}

simulator::ModuleType ModuleCatalog::ParseModuleType(
    const std::string& name) {
  if (name == "PREDICTION" || name == "prediction") {
    return simulator::PREDICTION;
  }
  if (name == "PLANNING" || name == "planning") {
    return simulator::PLANNING;
  }
  if (name == "CONTROL" || name == "control") {
    return simulator::CONTROL;
  }
  if (name == "ROUTING" || name == "routing") {
    return simulator::ROUTING;
  }
  return simulator::MODULE_UNKNOWN;
}

}  // namespace simulation
}  // namespace apollo
