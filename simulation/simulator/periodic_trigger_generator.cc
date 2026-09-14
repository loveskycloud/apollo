/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/periodic_trigger_generator.h"

namespace apollo {
namespace simulation {

std::vector<SimEvent> PeriodicTriggerGenerator::Generate(
    uint64_t begin_ns, uint64_t end_ns, uint32_t interval_ms,
    const std::string& channel) {
  std::vector<SimEvent> events;
  if (interval_ms == 0 || begin_ns > end_ns) {
    return events;
  }
  const uint64_t step_ns = static_cast<uint64_t>(interval_ms) * 1000000ULL;
  for (uint64_t t = begin_ns;;) {
    SimEvent ev;
    ev.sim_time_ns = t;
    ev.type = SimEventType::FABRICATED_MESSAGE;
    ev.tie_breaker = 30;
    ev.channel = channel;
    ev.payload = "";
    events.push_back(ev);
    if (end_ns - t < step_ns) {
      break;
    }
    t += step_ns;
  }
  return events;
}

}  // namespace simulation
}  // namespace apollo
