/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_SCENARIO_UTIL_H_
#define SIMULATION_SIMULATOR_SCENARIO_UTIL_H_

#include <string>
#include <vector>

#include "modules/simulation/simulator/proto/scenario.pb.h"

namespace apollo {
namespace simulation {

/**
 * ENABLE(CONTROL) — use with a local/member `scenario_` of type Scenario.
 * Example:
 *   if (ENABLE(CONTROL)) { dag_controller.LoadModule(CONTROL); }
 */
#define ENABLE(MODULE)                                                         \
  (::apollo::simulation::ScenarioUtil::IsEnabled(                              \
      scenario_, ::apollo::simulation::simulator::MODULE))

class ScenarioUtil {
 public:
  static bool IsEnabled(const simulator::Scenario& scenario,
                        simulator::ModuleType type);

  static simulator::Scenario BuildFromRuntimeModules(
      const std::vector<std::string>& runtime_modules,
      const std::string& scenario_id = "");
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_SCENARIO_UTIL_H_
