/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/scenario_util.h"

#include "cyber/common/log.h"
#include "modules/simulation/simulator/module_catalog.h"

namespace apollo {
namespace simulation {

bool ScenarioUtil::IsEnabled(const simulator::Scenario& scenario,
                             simulator::ModuleType type) {
  for (int i = 0; i < scenario.enabled_modules_size(); ++i) {
    if (scenario.enabled_modules(i) == type) {
      return true;
    }
  }
  return false;
}

simulator::Scenario ScenarioUtil::BuildFromRuntimeModules(
    const std::vector<std::string>& runtime_modules,
    const std::string& scenario_id) {
  simulator::Scenario scenario;
  scenario.set_mode(simulator::Scenario::LOGSIM);
  if (!scenario_id.empty()) {
    scenario.set_scenario_id(scenario_id);
    scenario.set_name(scenario_id);
  }
  for (const auto& name : runtime_modules) {
    const auto type = ModuleCatalog::ParseModuleType(name);
    if (type == simulator::MODULE_UNKNOWN) {
      AWARN << "unknown runtime module, skip: " << name;
      continue;
    }
    scenario.add_enabled_modules(type);
  }
  return scenario;
}

}  // namespace simulation
}  // namespace apollo
