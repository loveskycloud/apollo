/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_SIM_MONITOR_H_
#define SIMULATION_SIMULATOR_SIM_MONITOR_H_

#include <string>
#include <vector>

namespace apollo {
namespace simulation {

class SimMonitor {
 public:
  void SetModuleInitOk(bool ok) { module_init_ok_ = ok; }
  void IncInjected() { ++msg_injected_count_; }
  void IncProc(bool ok);
  bool HasFatal() const { return !fatal_errors_.empty(); }
  const std::vector<std::string>& fatal_errors() const { return fatal_errors_; }
  void AddFatal(const std::string& err);

 private:
  bool module_init_ok_ = false;
  uint64_t msg_injected_count_ = 0;
  uint64_t proc_count_ = 0;
  uint64_t proc_fail_count_ = 0;
  std::vector<std::string> fatal_errors_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_SIM_MONITOR_H_
