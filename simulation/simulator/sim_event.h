/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_SIM_EVENT_H_
#define SIMULATION_SIMULATOR_SIM_EVENT_H_

#include <cstdint>
#include <functional>
#include <string>

namespace apollo {
namespace simulation {

enum class SimEventType {
  FILE_MESSAGE = 0,
  TIMER_FIRE = 1,
  WARMUP_INJECT = 2,
  FABRICATED_MESSAGE = 3,
  SIM_END = 4,
};

struct SimEvent {
  uint64_t sim_time_ns = 0;
  SimEventType type = SimEventType::FILE_MESSAGE;
  int tie_breaker = 0;
  std::string channel;
  std::string payload;
  std::string module_name;
  // Stable source order for equal timestamp/channel messages. Never use pointer
  // addresses or arrival order from OS threads as a scheduling key.
  uint64_t sequence = 0;
  // Timer/world steps execute synchronously under the same frozen clock as bags.
  std::function<bool()> process;
  // Recurring timers keep only their next occurrence in the heap.
  uint64_t interval_ns = 0;
  uint64_t repeat_end_ns = 0;

  bool operator<(const SimEvent& other) const {
    if (sim_time_ns != other.sim_time_ns) {
      return sim_time_ns < other.sim_time_ns;
    }
    if (tie_breaker != other.tie_breaker) {
      return tie_breaker < other.tie_breaker;
    }
    if (channel != other.channel) {
      return channel < other.channel;
    }
    return sequence < other.sequence;
  }

  bool operator>(const SimEvent& other) const { return other < *this; }
};

struct TaskMeta {
  std::string module_name;
  uint64_t timestamp_ns = 0;
  int priority = 0;
};

struct BufferedMessage {
  uint64_t timestamp_ns = 0;
  std::string channel;
  std::string payload;
};

using ProcessCallback = std::function<void()>;

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_SIM_EVENT_H_
