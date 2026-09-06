/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_EGO_CAR_H_
#define SIMULATION_SIMULATOR_EGO_CAR_H_

#include <string>

#include "simulation/logsim/proto/simulation_task.pb.h"
#include "simulation/simulator/proto/scenario.pb.h"

namespace apollo {
namespace simulation {

/**
 * @brief Ego vehicle environment only (NOT module loading).
 *
 * Responsibilities:
 *   - Apply map_dir / vehicle_config gflags
 *   - VehicleConfigHelper::Init
 *   - HDMapUtil::ReloadMaps
 *   - Basic env sanity checks for LogSim warmup
 *
 * Module load/create/init belongs to DagController + SimClassLoader +
 * ClassController.
 */
class EgoCar {
 public:
  struct Options {
    std::string map_dir;
    std::string vehicle_config_path;
    std::string scenario_id;
  };

  EgoCar() = default;
  ~EgoCar() = default;

  EgoCar(const EgoCar&) = delete;
  EgoCar& operator=(const EgoCar&) = delete;

  static Options OptionsFromTask(const logsim::SimulationTask& task);
  static Options OptionsFromScenario(const simulator::Scenario& scenario);

  /** Initialize ego environment. Failure → caller should exit 1. */
  bool Init(const Options& opts);

  /** Convenience: prefer scenario map/vehicle, fall back to task. */
  bool Init(const simulator::Scenario& scenario,
            const logsim::SimulationTask& task);

  /**
   * Re-apply map/vehicle gflags and reload HDMap.
   * Component LoadConfigFiles(flagfile) typically resets FLAGS_map_dir to
   * sunnyvale_* via global_flagfile.txt — call this immediately after each
   * module Initialize if Init still sees wrong map, or inject override
   * flagfiles before Initialize (preferred).
   */
  bool ReapplyEnvironment();

  /** Write a flagfile that includes |base_flagfile| then overrides map/vehicle. */
  bool WriteOverrideFlagfile(const std::string& base_flagfile,
                             const std::string& out_path) const;

  bool ready() const { return ready_; }
  const Options& options() const { return options_; }

 private:
  bool ApplyGflags();
  bool InitVehicleConfig();
  bool InitHdMap();

  Options options_;
  bool ready_ = false;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_EGO_CAR_H_
