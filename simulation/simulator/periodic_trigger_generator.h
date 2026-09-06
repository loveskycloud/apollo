/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_PERIODIC_TRIGGER_GENERATOR_H_
#define SIMULATION_SIMULATOR_PERIODIC_TRIGGER_GENERATOR_H_

#include <cstdint>
#include <string>
#include <vector>

#include "simulation/simulator/sim_event.h"

namespace apollo {
namespace simulation {

class PeriodicTriggerGenerator {
 public:
  static std::vector<SimEvent> Generate(uint64_t begin_ns, uint64_t end_ns,
                                        uint32_t interval_ms,
                                        const std::string& channel);
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_PERIODIC_TRIGGER_GENERATOR_H_
