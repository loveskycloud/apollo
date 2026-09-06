/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include <utility>

#include "simulation/simulator/virtual_timer_scheduler.h"

#include "cyber/timer/sim_timer_registry.h"

namespace apollo {
namespace simulation {

std::vector<SimEvent> VirtualTimerScheduler::BuildEvents(
    uint64_t begin_ns, uint64_t end_ns) const {
  std::vector<SimEvent> all;
  for (const auto& entry : cyber::SimTimerRegistry::Instance()->GetAll()) {
    auto events = PeriodicTriggerGenerator::Generate(
        begin_ns, end_ns, entry.interval_ms,
        "/apollo/simulation/periodic_trigger/" + entry.name);
    all.insert(all.end(), events.begin(), events.end());
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
