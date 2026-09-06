/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_EMULATOR_MESSAGE_QUEUE_H_
#define SIMULATION_SIMULATOR_EMULATOR_MESSAGE_QUEUE_H_

#include <functional>
#include <mutex>
#include <queue>
#include <vector>

#include "simulation/simulator/sim_event.h"

namespace apollo {
namespace simulation {

class EmulatorMessageQueue {
 public:
  void Push(SimEvent ev);
  bool Empty() const;
  bool Peek(SimEvent* out) const;
  bool Pop(SimEvent* out);
  size_t Size() const;

 private:
  mutable std::mutex mutex_;
  std::priority_queue<SimEvent, std::vector<SimEvent>, std::greater<SimEvent>>
      queue_;
};

class FabricatedMessageQueue {
 public:
  void Push(SimEvent ev);
  bool Empty() const;
  std::vector<SimEvent> DrainAll();

 private:
  mutable std::mutex mutex_;
  std::priority_queue<SimEvent, std::vector<SimEvent>, std::greater<SimEvent>>
      queue_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_EMULATOR_MESSAGE_QUEUE_H_
