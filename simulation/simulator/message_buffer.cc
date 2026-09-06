/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/message_buffer.h"

namespace apollo {
namespace simulation {

void MessageBuffer::Push(const BufferedMessage& msg) {
  by_channel_[msg.channel].push_back(msg);
}

std::vector<BufferedMessage> MessageBuffer::PickRequiredDefault(
    const std::string& /*module_name*/, uint64_t task_timestamp_ns,
    const std::vector<std::string>& subscribe_channels) const {
  std::vector<BufferedMessage> picked;
  for (const auto& channel : subscribe_channels) {
    auto it = by_channel_.find(channel);
    if (it == by_channel_.end()) {
      continue;
    }
    for (const auto& msg : it->second) {
      if (msg.timestamp_ns <= task_timestamp_ns) {
        picked.push_back(msg);
      }
    }
  }
  return picked;
}

}  // namespace simulation
}  // namespace apollo
