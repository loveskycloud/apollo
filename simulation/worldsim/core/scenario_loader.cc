/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#include "modules/simulation/worldsim/core/scenario_loader.h"

#include <fstream>
#include <sstream>

#include "cyber/common/log.h"
#include "google/protobuf/util/json_util.h"

namespace apollo {
namespace simulation {
namespace worldsim {

bool ScenarioLoader::LoadFromJsonString(const std::string& json,
                                        Scenario* scenario) {
  if (scenario == nullptr) {
    return false;
  }
  scenario->Clear();
  google::protobuf::util::JsonParseOptions options;
  options.ignore_unknown_fields = false;
  const auto status =
      google::protobuf::util::JsonStringToMessage(json, scenario, options);
  if (!status.ok()) {
    AERROR << "Scenario JSON parse failed: " << status.message();
    return false;
  }
  return true;
}

bool ScenarioLoader::LoadFromJsonFile(const std::string& path,
                                      Scenario* scenario) {
  std::ifstream ifs(path);
  if (!ifs.is_open()) {
    AERROR << "Cannot open scenario file: " << path;
    return false;
  }
  std::stringstream buffer;
  buffer << ifs.rdbuf();
  return LoadFromJsonString(buffer.str(), scenario);
}

bool ScenarioLoader::LoadWorldFromJsonFile(const std::string& path,
                                           World* world) {
  Scenario scenario;
  if (!LoadFromJsonFile(path, &scenario)) {
    return false;
  }
  return world->Load(scenario);
}

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
