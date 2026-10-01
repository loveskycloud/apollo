/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_COMPUTATIONAL_GRAPH_H_
#define SIMULATION_SIMULATOR_COMPUTATIONAL_GRAPH_H_

#include <map>
#include <string>
#include <vector>

#include "modules/simulation/simulator/proto/module_trigger.pb.h"

namespace apollo {
namespace simulation {

class ComputationalGraph {
 public:
  bool LoadFromTable(const simulator::ModuleTriggerTable& table);
  int GetPriority(const std::string& module_name) const;
  const simulator::ModuleTriggerSpec* GetSpec(
      const std::string& module_name) const;
  const std::map<std::string, simulator::ModuleTriggerSpec>& modules() const {
    return modules_;
  }

 private:
  std::map<std::string, simulator::ModuleTriggerSpec> modules_;
  std::map<std::string, int> priority_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_COMPUTATIONAL_GRAPH_H_
