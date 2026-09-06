/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_MESSAGE_BUFFER_H_
#define SIMULATION_SIMULATOR_MESSAGE_BUFFER_H_

#include <map>
#include <string>
#include <vector>

#include "simulation/simulator/sim_event.h"

namespace apollo {
namespace simulation {

class MessageBuffer {
 public:
  void Push(const BufferedMessage& msg);
  std::vector<BufferedMessage> PickRequiredDefault(
      const std::string& module_name, uint64_t task_timestamp_ns,
      const std::vector<std::string>& subscribe_channels) const;

 private:
  std::map<std::string, std::vector<BufferedMessage>> by_channel_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_MESSAGE_BUFFER_H_
