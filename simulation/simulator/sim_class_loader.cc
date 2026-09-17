/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/sim_class_loader.h"

#include "cyber/common/log.h"

namespace apollo {
namespace simulation {

bool SimClassLoader::LoadLibrary(const std::string& library_path) {
  if (library_path.empty()) {
    AERROR << "empty library path";
    return false;
  }
  if (!loader_manager_.LoadLibrary(library_path)) {
    AERROR << "failed to load library: " << library_path;
    return false;
  }
  AINFO << "SimClassLoader loaded: " << library_path;
  return true;
}

void SimClassLoader::UnloadAll() { loader_manager_.UnloadAllLibrary(); }

bool SimClassLoader::IsClassRegistered(const std::string& class_name) const {
  return loader_manager_.IsClassValid<cyber::ComponentBase>(class_name);
}

std::shared_ptr<cyber::ComponentBase> SimClassLoader::Create(
    const std::string& class_name) {
  auto obj =
      loader_manager_.CreateClassObj<cyber::ComponentBase>(class_name);
  if (!obj) {
    AERROR << "SimClassLoader create failed, class_name=" << class_name;
  }
  return obj;
}

std::shared_ptr<cyber::ComponentBase> SimClassLoader::Create(
    const std::string& class_name, const std::string& library_path) {
  auto obj = loader_manager_.CreateClassObj<cyber::ComponentBase>(
      class_name, library_path);
  if (!obj) {
    AERROR << "SimClassLoader create failed, class_name=" << class_name
           << " library=" << library_path;
  }
  return obj;
}

std::vector<std::string> SimClassLoader::RegisteredClassNames() const {
  return loader_manager_.GetValidClassNames<cyber::ComponentBase>();
}

}  // namespace simulation
}  // namespace apollo
