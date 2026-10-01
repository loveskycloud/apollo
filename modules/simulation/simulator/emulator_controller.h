/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_EMULATOR_CONTROLLER_H_
#define SIMULATION_SIMULATOR_EMULATOR_CONTROLLER_H_

#include <chrono>
#include <memory>
#include <set>
#include <string>

#include "modules/simulation/logsim/proto/channel_policy.pb.h"
#include "modules/simulation/simulator/emulator_message_queue.h"
#include "modules/simulation/simulator/i_message_source.h"
#include "modules/simulation/simulator/message_consumer.h"

namespace apollo {
namespace simulation {

class EmulatorController {
 public:
  struct Options {
    std::shared_ptr<IMessageSource> source;
    MessageConsumer* consumer = nullptr;
    logsim::ChannelPolicy channel_policy;
  };

  bool Init(const Options& opts);
  bool LoadFromSource();
  void MergeFabricated(FabricatedMessageQueue* fabricated);
  bool PublishNext();
  bool WaitAndPublishNext(std::chrono::milliseconds feedback_timeout);
  const std::string& error() const { return error_; }
  uint64_t current_time_ns() const { return frozen_clock_ns_; }
  const std::string& current_channel() const { return current_channel_; }

 private:
  bool ShouldInject(const std::string& channel) const;
  bool ShouldSuppress(const std::string& channel) const;

  std::shared_ptr<IMessageSource> source_;
  MessageConsumer* consumer_ = nullptr;
  logsim::ChannelPolicy channel_policy_;
  EmulatorMessageQueue message_queue_;
  uint64_t frozen_clock_ns_ = 0;
  std::string current_channel_;
  std::string error_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_EMULATOR_CONTROLLER_H_
