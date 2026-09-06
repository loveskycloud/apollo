/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_EMULATOR_CONTROLLER_H_
#define SIMULATION_SIMULATOR_EMULATOR_CONTROLLER_H_

#include <atomic>
#include <chrono>
#include <memory>
#include <set>
#include <string>

#include "simulation/logsim/proto/channel_policy.pb.h"
#include "simulation/simulator/emulator_message_queue.h"
#include "simulation/simulator/i_message_source.h"
#include "simulation/simulator/message_buffer.h"
#include "simulation/simulator/message_consumer.h"
#include "simulation/simulator/sim_scheduler.h"

namespace apollo {
namespace simulation {

class EmulatorController {
 public:
  struct Options {
    std::shared_ptr<IMessageSource> source;
    MessageConsumer* consumer = nullptr;
    SimScheduler* scheduler = nullptr;
    MessageBuffer* global_buffer = nullptr;
    logsim::ChannelPolicy channel_policy;
  };

  bool Init(const Options& opts);
  bool LoadFromSource();
  void MergeFabricated(FabricatedMessageQueue* fabricated);
  bool PublishNext();
  bool WaitAndPublishNext(std::chrono::milliseconds feedback_timeout);

 private:
  bool ShouldInject(const std::string& channel) const;
  bool ShouldSuppress(const std::string& channel) const;
  void BufferMessage(const SimEvent& ev);

  std::shared_ptr<IMessageSource> source_;
  MessageConsumer* consumer_ = nullptr;
  SimScheduler* scheduler_ = nullptr;
  MessageBuffer* global_buffer_ = nullptr;
  logsim::ChannelPolicy channel_policy_;
  EmulatorMessageQueue message_queue_;
  std::atomic<bool> round_active_{false};
  uint64_t frozen_clock_ns_ = 0;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_EMULATOR_CONTROLLER_H_
