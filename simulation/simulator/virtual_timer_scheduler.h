/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_VIRTUAL_TIMER_SCHEDULER_H_
#define SIMULATION_SIMULATOR_VIRTUAL_TIMER_SCHEDULER_H_

#include <vector>

#include "simulation/simulator/emulator_message_queue.h"
#include "simulation/simulator/periodic_trigger_generator.h"
#include "simulation/simulator/sim_event.h"

namespace apollo {
namespace simulation {

class VirtualTimerScheduler {
 public:
  std::vector<SimEvent> BuildEvents(uint64_t begin_ns, uint64_t end_ns) const;
  void RegisterFromSimTimerRegistry(uint64_t begin_ns, uint64_t end_ns,
                                    FabricatedMessageQueue* queue) const;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_VIRTUAL_TIMER_SCHEDULER_H_
