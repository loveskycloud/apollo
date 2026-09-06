/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "cyber/timer/sim_timer_registry.h"

namespace apollo {
namespace cyber {

SimTimerRegistry::SimTimerRegistry() {}

void SimTimerRegistry::Register(const std::string& name, uint32_t interval_ms,
                                const std::function<void()>& process) {
  std::lock_guard<std::mutex> lock(mutex_);
  for (auto& entry : entries_) {
    if (entry.name == name) {
      entry.interval_ms = interval_ms;
      entry.process = process;
      return;
    }
  }
  entries_.push_back(SimTimerEntry{name, interval_ms, process});
}

std::vector<SimTimerEntry> SimTimerRegistry::GetAll() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return entries_;
}

void SimTimerRegistry::Clear() {
  std::lock_guard<std::mutex> lock(mutex_);
  entries_.clear();
}

}  // namespace cyber
}  // namespace apollo
