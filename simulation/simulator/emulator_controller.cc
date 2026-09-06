/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include <utility>

#include "simulation/simulator/emulator_controller.h"

#include "cyber/time/clock.h"

namespace apollo {
namespace simulation {

bool EmulatorController::ShouldInject(const std::string& channel) const {
  if (channel_policy_.inject_channels().empty()) {
    return true;
  }
  for (const auto& c : channel_policy_.inject_channels()) {
    if (c == channel) {
      return true;
    }
  }
  return false;
}

bool EmulatorController::ShouldSuppress(const std::string& channel) const {
  for (const auto& c : channel_policy_.suppress_channels()) {
    if (c == channel) {
      return true;
    }
  }
  return false;
}

bool EmulatorController::Init(const Options& opts) {
  source_ = opts.source;
  consumer_ = opts.consumer;
  scheduler_ = opts.scheduler;
  global_buffer_ = opts.global_buffer;
  channel_policy_ = opts.channel_policy;
  return source_ != nullptr && consumer_ != nullptr;
}

bool EmulatorController::LoadFromSource() {
  if (!source_) {
    return false;
  }
  while (source_->HasNext()) {
    SimEvent ev;
    if (!source_->Next(&ev)) {
      break;
    }
    if (ShouldSuppress(ev.channel)) {
      continue;
    }
    message_queue_.Push(std::move(ev));
  }
  return true;
}

void EmulatorController::MergeFabricated(FabricatedMessageQueue* fabricated) {
  if (!fabricated) {
    return;
  }
  for (auto& ev : fabricated->DrainAll()) {
    message_queue_.Push(std::move(ev));
  }
}

void EmulatorController::BufferMessage(const SimEvent& ev) {
  if (!global_buffer_) {
    return;
  }
  BufferedMessage msg;
  msg.timestamp_ns = ev.sim_time_ns;
  msg.channel = ev.channel;
  msg.payload = ev.payload;
  global_buffer_->Push(msg);
  if (scheduler_) {
    scheduler_->OnMessageReceived(msg, ev.sim_time_ns);
  }
}

bool EmulatorController::PublishNext() {
  SimEvent ev;
  if (!message_queue_.Pop(&ev)) {
    return false;
  }
  if (!ShouldInject(ev.channel)) {
    BufferMessage(ev);
    return true;
  }
  cyber::Clock::SetNow(cyber::Time(ev.sim_time_ns));
  frozen_clock_ns_ = ev.sim_time_ns;
  round_active_ = true;
  if (!consumer_->Publish(ev.channel, ev.payload)) {
    return false;
  }
  BufferMessage(ev);
  if (scheduler_ && global_buffer_) {
    scheduler_->RunOneRound(frozen_clock_ns_, global_buffer_);
  }
  round_active_ = false;
  if (scheduler_) {
    scheduler_->SignalFeedback();
  }
  return true;
}

bool EmulatorController::WaitAndPublishNext(
    std::chrono::milliseconds feedback_timeout) {
  if (scheduler_ && round_active_) {
    scheduler_->WaitForFeedback(feedback_timeout);
  }
  return PublishNext();
}

}  // namespace simulation
}  // namespace apollo
