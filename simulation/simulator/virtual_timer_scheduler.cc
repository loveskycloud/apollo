/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include <utility>

#include "modules/simulation/simulator/virtual_timer_scheduler.h"

#include "cyber/timer/sim_timer_registry.h"

namespace apollo {
namespace simulation {

std::vector<SimEvent> VirtualTimerScheduler::BuildEvents(
    uint64_t begin_ns, uint64_t end_ns) const {
  std::vector<SimEvent> all;
  if (begin_ns > end_ns) { return all; }
  for (const auto& entry : cyber::SimTimerRegistry::Instance()->GetAll()) {
    auto events = PeriodicTriggerGenerator::Generate(
        begin_ns, begin_ns, entry.interval_ms,
        "/apollo/simulation/periodic_trigger/" + entry.name);
    for (auto& event : events) {
      event.type = SimEventType::TIMER_FIRE;
      event.module_name = entry.name;
      event.interval_ns = static_cast<uint64_t>(entry.interval_ms) * 1000000;
      event.repeat_end_ns = end_ns;
      event.process = [process = entry.process]() {
        if (!process) {
          return false;
        }
        process();
        return true;
      };
      all.push_back(std::move(event));
    }
  }
  return all;
}

void VirtualTimerScheduler::RegisterFromSimTimerRegistry(
    uint64_t begin_ns, uint64_t end_ns,
    FabricatedMessageQueue* queue) const {
  if (!queue) {
    return;
  }
  for (auto& ev : BuildEvents(begin_ns, end_ns)) {
    queue->Push(std::move(ev));
  }
}

}  // namespace simulation
}  // namespace apollo
