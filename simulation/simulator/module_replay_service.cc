/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/module_replay_service.h"

namespace apollo {
namespace simulation {

ModuleReplayService::ModuleReplayService(const std::string& module_name)
    : module_name_(module_name) {}

void ModuleReplayService::SetSpec(const simulator::ModuleTriggerSpec& spec) {
  spec_ = spec;
}

bool ModuleReplayService::IsTriggerMessage(const std::string& channel) const {
  if (spec_.kind() == simulator::PERIODIC_MSG_TRIGGERED) {
    return channel == spec_.periodic_trigger_channel();
  }
  return channel == spec_.trigger_channel();
}

void ModuleReplayService::OnMessage(const BufferedMessage& msg,
                                    uint64_t sim_clock_ns,
                                    bool* ready_to_trigger) {
  buffer_.Push(msg);
  if (ready_to_trigger != nullptr) {
    *ready_to_trigger = false;
  }
  if (!IsTriggerMessage(msg.channel)) {
    return;
  }
  pending_trigger_time_ns_ = msg.timestamp_ns;
  if (msg.timestamp_ns <= sim_clock_ns) {
    readiness_ = TaskReadiness::READY;
    if (ready_to_trigger != nullptr) {
      *ready_to_trigger = true;
    }
  } else {
    readiness_ = TaskReadiness::NOT_READY;
  }
}

void ModuleReplayService::MarkTriggered() {
  readiness_ = TaskReadiness::NOT_READY;
}

}  // namespace simulation
}  // namespace apollo
