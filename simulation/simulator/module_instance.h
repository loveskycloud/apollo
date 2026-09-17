/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_MODULE_INSTANCE_H_
#define SIMULATION_SIMULATOR_MODULE_INSTANCE_H_

#include <memory>
#include <string>

#include "cyber/component/component_base.h"
#include "modules/simulation/simulator/proto/scenario.pb.h"

namespace apollo {
namespace simulation {

/** One created + initialized component instance. */
struct ModuleInstance {
  simulator::ModuleType type = simulator::MODULE_UNKNOWN;
  std::string module_name;
  std::string class_name;
  std::string dag_path;
  std::string library_path;
  std::shared_ptr<cyber::ComponentBase> component;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_MODULE_INSTANCE_H_
