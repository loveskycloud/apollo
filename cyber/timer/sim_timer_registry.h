/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef CYBER_TIMER_SIM_TIMER_REGISTRY_H_
#define CYBER_TIMER_SIM_TIMER_REGISTRY_H_

#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <vector>

#include "cyber/common/macros.h"

namespace apollo {
namespace cyber {

struct SimTimerEntry {
  std::string name;
  uint32_t interval_ms = 0;
  std::function<void()> process;
};

class SimTimerRegistry {
 public:
  void Register(const std::string& name, uint32_t interval_ms,
                const std::function<void()>& process);
  std::vector<SimTimerEntry> GetAll() const;
  void Clear();

 private:
  mutable std::mutex mutex_;
  std::vector<SimTimerEntry> entries_;

  DECLARE_SINGLETON(SimTimerRegistry)
};

}  // namespace cyber
}  // namespace apollo

#endif  // CYBER_TIMER_SIM_TIMER_REGISTRY_H_
