/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/computational_graph.h"

namespace apollo {
namespace simulation {

bool ComputationalGraph::LoadFromTable(
    const simulator::ModuleTriggerTable& table) {
  modules_.clear();
  priority_.clear();
  int order = 0;
  for (const auto& spec : table.modules()) {
    modules_[spec.module_name()] = spec;
    priority_[spec.module_name()] = order++;
  }
  return !modules_.empty();
}

int ComputationalGraph::GetPriority(const std::string& module_name) const {
  auto it = priority_.find(module_name);
  if (it == priority_.end()) {
    return 9999;
  }
  return it->second;
}

const simulator::ModuleTriggerSpec* ComputationalGraph::GetSpec(
    const std::string& module_name) const {
  auto it = modules_.find(module_name);
  if (it == modules_.end()) {
    return nullptr;
  }
  return &it->second;
}

}  // namespace simulation
}  // namespace apollo
