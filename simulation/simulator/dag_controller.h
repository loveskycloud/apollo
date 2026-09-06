/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_DAG_CONTROLLER_H_
#define SIMULATION_SIMULATOR_DAG_CONTROLLER_H_

#include <memory>
#include <string>
#include <vector>

#include "cyber/proto/dag_conf.pb.h"
#include "simulation/simulator/class_controller.h"
#include "simulation/simulator/ego_car.h"
#include "simulation/simulator/module_catalog.h"
#include "simulation/simulator/module_instance.h"
#include "simulation/simulator/proto/scenario.pb.h"
#include "simulation/simulator/scenario_util.h"
#include "simulation/simulator/sim_class_loader.h"

namespace apollo {
namespace simulation {

/**
 * @brief Orchestrate enabled modules from Scenario:
 *   if (ENABLE(M)) { LoadDag → LoadLibrary → Create/Init → list }
 *
 * Owns SimClassLoader + ClassController. Does not own ego environment.
 */
class DagController {
 public:
  DagController();
  ~DagController();

  DagController(const DagController&) = delete;
  DagController& operator=(const DagController&) = delete;

  /**
   * Start by Scenario.enabled_modules.
   * @param ego optional; when set, rewrite each component flagfile so
   *        map_dir/vehicle override global_flagfile sunnyvale defaults.
   * @param flag_out_dir directory for generated override flagfiles
   *        (typically task_dir/sim_flags).
   */
  bool Start(const simulator::Scenario& scenario, EgoCar* ego = nullptr,
             const std::string& flag_out_dir = "");

  bool LoadModule(simulator::ModuleType type);

  void Shutdown();

  const simulator::Scenario& scenario() const { return scenario_; }

  const std::vector<ModuleInstance>& module_list() const {
    return class_controller_.module_list();
  }

  size_t module_count() const { return class_controller_.size(); }

  bool HasModule(simulator::ModuleType type) const {
    return class_controller_.HasModule(type);
  }

  SimClassLoader* class_loader() { return &class_loader_; }
  ClassController* class_controller() { return &class_controller_; }

 private:
  bool LoadPlugins();
  bool LoadDag(const simulator::ModuleSpec& spec,
               cyber::proto::DagConfig* dag_config,
               std::string* resolved_dag_path);
  bool LoadLibraryForDag(const cyber::proto::DagConfig& dag_config,
                         const simulator::ModuleSpec& spec,
                         std::string* resolved_library_path);
  bool InjectEgoFlagOverrides(cyber::proto::DagConfig* dag_config,
                              const std::string& module_tag);
  void UpdateComponentNums(const cyber::proto::DagConfig& dag_config);

  simulator::Scenario scenario_;
  EgoCar* ego_ = nullptr;  // not owned
  std::string flag_out_dir_;
  SimClassLoader class_loader_;
  ClassController class_controller_;
  bool started_ = false;
  int total_component_nums_ = 0;
  bool has_timer_component_ = false;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_DAG_CONTROLLER_H_
