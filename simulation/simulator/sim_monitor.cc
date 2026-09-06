/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/sim_monitor.h"

namespace apollo {
namespace simulation {

void SimMonitor::IncProc(bool ok) {
  ++proc_count_;
  if (!ok) {
    ++proc_fail_count_;
  }
}

void SimMonitor::AddFatal(const std::string& err) {
  fatal_errors_.push_back(err);
}

}  // namespace simulation
}  // namespace apollo
