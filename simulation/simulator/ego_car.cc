/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/ego_car.h"

#include <fstream>
#include <iomanip>
#include <limits>

#include "cyber/common/file.h"
#include "cyber/common/log.h"
#include "gflags/gflags.h"
#include "modules/common/configs/config_gflags.h"
#include "modules/common/configs/vehicle_config_helper.h"
#include "modules/map/hdmap/hdmap_util.h"
#include "simulation/simulator/environment_tools.h"

namespace apollo {
namespace simulation {

EgoCar::Options EgoCar::OptionsFromTask(
    const logsim::SimulationTask& task) {
  Options opts;
  opts.map_dir = task.map_dir();
  opts.vehicle_config_path = task.vehicle_config_path();
  opts.scenario_id = task.scenario_id();
  return opts;
}

EgoCar::Options EgoCar::OptionsFromScenario(
    const simulator::Scenario& scenario) {
  Options opts;
  opts.map_dir = scenario.map_dir();
  opts.vehicle_config_path = scenario.vehicle();
  opts.scenario_id = scenario.scenario_id();
  return opts;
}

bool EgoCar::Init(const simulator::Scenario& scenario,
                  const logsim::SimulationTask& task) {
  Options opts = OptionsFromTask(task);
  const Options from_scenario = OptionsFromScenario(scenario);
  if (!from_scenario.map_dir.empty()) {
    opts.map_dir = from_scenario.map_dir;
  }
  if (!from_scenario.vehicle_config_path.empty()) {
    opts.vehicle_config_path = from_scenario.vehicle_config_path;
  }
  if (!from_scenario.scenario_id.empty()) {
    opts.scenario_id = from_scenario.scenario_id;
  }
  return Init(opts);
}

bool EgoCar::Init(const Options& opts) {
  options_ = opts;
  ready_ = false;

  if (options_.map_dir.empty()) {
    AERROR << "EgoCar: map_dir is empty";
    return false;
  }
  if (options_.vehicle_config_path.empty()) {
    AERROR << "EgoCar: vehicle_config_path is empty";
    return false;
  }
  if (!cyber::common::DirectoryExists(options_.map_dir) &&
      !cyber::common::PathExists(options_.map_dir)) {
    AERROR << "EgoCar: map_dir not found: " << options_.map_dir;
    return false;
  }
  if (!cyber::common::PathExists(options_.vehicle_config_path)) {
    AERROR << "EgoCar: vehicle config not found: "
           << options_.vehicle_config_path;
    return false;
  }

  if (!ReadHalfVehicleWidth(options_.vehicle_config_path, &half_vehicle_width_) ||
      !ApplyGflags()) {
    return false;
  }
  if (!InitVehicleConfig()) {
    return false;
  }
  if (!InitHdMap()) {
    return false;
  }

  ready_ = true;
  AINFO << "EgoCar environment ready"
        << " scenario_id=" << options_.scenario_id
        << " map_dir=" << options_.map_dir
        << " vehicle=" << options_.vehicle_config_path;
  return true;
}

bool EgoCar::ApplyGflags() {
  return ApplyMapVehicleFlags(options_.map_dir, options_.vehicle_config_path,
                              half_vehicle_width_);
}

bool EgoCar::VerifyEnvironment() const {
  return VerifyMapVehicleFlags(options_.map_dir, options_.vehicle_config_path,
                               half_vehicle_width_);
}

bool EgoCar::ReapplyEnvironment() {
  if (options_.map_dir.empty() || options_.vehicle_config_path.empty()) {
    AERROR << "EgoCar::ReapplyEnvironment: missing map/vehicle";
    return false;
  }
  if (!ApplyGflags()) {
    return false;
  }
  if (!InitVehicleConfig()) {
    return false;
  }
  return InitHdMap();
}

bool EgoCar::WriteOverrideFlagfile(const std::string& base_flagfile,
                                   const std::string& out_path) const {
  if (options_.map_dir.empty() || options_.vehicle_config_path.empty()) {
    AERROR << "EgoCar::WriteOverrideFlagfile: missing map/vehicle";
    return false;
  }
  const std::string dir = cyber::common::GetDirName(out_path);
  if (!dir.empty() && !cyber::common::EnsureDirectory(dir)) {
    AERROR << "EgoCar: failed to create flag dir: " << dir;
    return false;
  }
  std::ofstream ofs(out_path);
  if (!ofs.is_open()) {
    AERROR << "EgoCar: cannot write override flagfile: " << out_path;
    return false;
  }
  // Keep module defaults, then override map/vehicle last (gflags order).
  if (!base_flagfile.empty()) {
    ofs << "--flagfile=" << base_flagfile << "\n";
  }
  ofs << "--map_dir=" << options_.map_dir << "\n";
  ofs << "--vehicle_config_path=" << options_.vehicle_config_path << "\n";
  ofs << "--half_vehicle_width="
      << std::setprecision(std::numeric_limits<double>::max_digits10)
      << half_vehicle_width_ << "\n";
  // Freeze algorithm scheduling too, not only the publisher's clock. These are
  // run-local overrides and never change the selected profile on disk.
  if (out_path.find("PREDICTION") != std::string::npos) {
    ofs << "--enable_multi_thread=false\n--max_thread_num=1\n--max_caution_thread_num=1\n";
  }
  if (out_path.find("PLANNING") != std::string::npos) {
    ofs << "--enable_reference_line_provider_thread=false\n"
           "--use_multi_thread_to_add_obstacles=false\n";
  }
  ofs.close();
  AINFO << "EgoCar wrote override flagfile: " << out_path
        << " map_dir=" << options_.map_dir;
  return true;
}

bool EgoCar::InitVehicleConfig() {
  apollo::common::VehicleConfigHelper::Init(options_.vehicle_config_path);
  AINFO << "EgoCar VehicleConfigHelper initialized";
  return true;
}

bool EgoCar::InitHdMap() {
  if (!apollo::hdmap::HDMapUtil::ReloadMaps()) {
    AERROR << "EgoCar HDMapUtil::ReloadMaps failed, map_dir="
           << options_.map_dir;
    return false;
  }
  AINFO << "EgoCar HDMap reloaded";
  return true;
}

}  // namespace simulation
}  // namespace apollo
