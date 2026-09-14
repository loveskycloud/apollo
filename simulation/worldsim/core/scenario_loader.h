/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#pragma once

#include <string>

#include "simulation/worldsim/core/world.h"
#include "simulation/worldsim/proto/scenario.pb.h"

namespace apollo {
namespace simulation {
namespace worldsim {

class ScenarioLoader {
 public:
  /** Load Scenario proto from JSON file (protobuf JSON format). */
  static bool LoadFromJsonFile(const std::string& path, Scenario* scenario);
  /** Parse Scenario from JSON string. */
  static bool LoadFromJsonString(const std::string& json, Scenario* scenario);
  /** Convenience: load into World. */
  static bool LoadWorldFromJsonFile(const std::string& path, World* world);
};

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
