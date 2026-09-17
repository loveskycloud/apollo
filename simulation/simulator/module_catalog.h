/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_MODULE_CATALOG_H_
#define SIMULATION_SIMULATOR_MODULE_CATALOG_H_

#include <string>
#include <unordered_map>
#include <vector>

#include "modules/simulation/simulator/proto/scenario.pb.h"

namespace apollo {
namespace simulation {

/** Built-in mapping: ModuleType → default dag / display name. */
class ModuleCatalog {
 public:
  static const ModuleCatalog& Instance();

  bool Has(simulator::ModuleType type) const;
  simulator::ModuleSpec GetDefaultSpec(simulator::ModuleType type) const;

  /** Merge scenario.module_specs overrides onto defaults. */
  simulator::ModuleSpec ResolveSpec(
      simulator::ModuleType type,
      const simulator::Scenario& scenario) const;

  static std::string ModuleTypeName(simulator::ModuleType type);
  static simulator::ModuleType ParseModuleType(const std::string& name);

 private:
  ModuleCatalog();
  std::unordered_map<int, simulator::ModuleSpec> defaults_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_MODULE_CATALOG_H_
