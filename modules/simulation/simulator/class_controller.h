/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_CLASS_CONTROLLER_H_
#define SIMULATION_SIMULATOR_CLASS_CONTROLLER_H_

#include <memory>
#include <string>
#include <vector>

#include "cyber/proto/dag_conf.pb.h"
#include "modules/simulation/simulator/module_instance.h"
#include "modules/simulation/simulator/sim_class_loader.h"

namespace apollo {
namespace simulation {

/**
 * @brief Create + Initialize components, own the instance list.
 *
 * Does not load dag files; receives parsed DagConfig + resolved .so path.
 */
class ClassController {
 public:
  explicit ClassController(SimClassLoader* class_loader);

  /**
   * Reflect Create(class_name) → Initialize(config) → push to module_list_.
   */
  bool CreateInitAndAppend(const cyber::proto::DagConfig& dag_config,
                           simulator::ModuleType type,
                           const std::string& module_name,
                           const std::string& dag_path,
                           const std::string& library_path);

  void Shutdown();

  const std::vector<ModuleInstance>& module_list() const {
    return module_list_;
  }

  size_t size() const { return module_list_.size(); }

  bool HasModule(simulator::ModuleType type) const;

 private:
  SimClassLoader* class_loader_ = nullptr;  // not owned
  std::vector<ModuleInstance> module_list_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_CLASS_CONTROLLER_H_
