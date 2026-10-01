/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_SIM_CLASS_LOADER_H_
#define SIMULATION_SIMULATOR_SIM_CLASS_LOADER_H_

#include <memory>
#include <string>
#include <vector>

#include "cyber/class_loader/class_loader_manager.h"
#include "cyber/component/component_base.h"

namespace apollo {
namespace simulation {

/**
 * @brief Load .so and resolve constructors by class_name.
 *
 * Shared libraries register factories via CLASS_LOADER_REGISTER_CLASS;
 * Create() reflects the constructor and returns ComponentBase.
 */
class SimClassLoader {
 public:
  bool LoadLibrary(const std::string& library_path);
  void UnloadAll();

  bool IsClassRegistered(const std::string& class_name) const;

  std::shared_ptr<cyber::ComponentBase> Create(
      const std::string& class_name);

  std::shared_ptr<cyber::ComponentBase> Create(
      const std::string& class_name, const std::string& library_path);

  std::vector<std::string> RegisteredClassNames() const;

 private:
  mutable cyber::class_loader::ClassLoaderManager loader_manager_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_SIM_CLASS_LOADER_H_
