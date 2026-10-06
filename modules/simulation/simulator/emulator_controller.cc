/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include <utility>

#include "modules/simulation/simulator/emulator_controller.h"

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
  channel_policy_ = opts.channel_policy;
  record_bag_reference_ = opts.record_bag_reference;
  error_.clear();
  frozen_clock_ns_ = 0;
  return source_ != nullptr && consumer_ != nullptr;
}

bool EmulatorController::LoadFromSource() {
  if (!source_) {
    return false;
  }
  // Keep the source lazy: a world tick must see outputs from the preceding
  // round, not precompute an open-loop movie before algorithms are started.
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

bool EmulatorController::PublishNext() {
  SimEvent ev;
  while (true) {
    SimEvent input, timer;
    const bool has_input = source_->Peek(&input);
    // Source end_ns can shrink (e.g. WorldSim mission_complete). Do not run
    // algorithm timers past the live source horizon or pose/chassis stop while
    // PnC keeps ticking on a stale localization.
    const bool has_timer =
        message_queue_.Peek(&timer) && timer.sim_time_ns <= source_->end_ns();
    if (!has_input && source_->HasNext()) {
      error_ = "source Peek failed before EOF";
      return false;
    }
    if (!has_input && !has_timer) {
      return false;
    }
    if (has_input && (!has_timer || !(timer < input))) {
      if (!source_->Next(&ev)) {
        error_ = "source Next failed before EOF";
        return false;
      }
    } else if (!message_queue_.Pop(&ev)) {
      error_ = "timer queue changed during synchronous execution";
      return false;
    }
    if (ev.bag_reference || ev.process || ev.type == SimEventType::TIMER_FIRE ||
        (!ShouldSuppress(ev.channel) && ShouldInject(ev.channel))) {
      break;
    }
  }
  if (ev.sim_time_ns < frozen_clock_ns_) {
    error_ = "non-monotonic event time on " + ev.channel;
    return false;
  }
  cyber::Clock::SetNow(cyber::Time(ev.sim_time_ns));
  frozen_clock_ns_ = ev.sim_time_ns;
  current_channel_ = ev.channel;
  if (ev.bag_reference) {
    if (!record_bag_reference_ || !record_bag_reference_(ev)) {
      error_ = "bag reference recording failed: " + ev.bag_reference->target_topic;
      return false;
    }
    if (ShouldSuppress(ev.channel) || !ShouldInject(ev.channel)) {
      return true;
    }
  }
  // MODE_SIMULATION Intra publication completes its dependent callbacks before
  // returning. Advancing the clock before this returns would violate causality.
  const bool ok = ev.process ? ev.process()
                            : ev.type != SimEventType::TIMER_FIRE &&
                                  consumer_->Publish(ev.channel, ev.payload);
  if (!ok) {
    error_ = "event execution failed: " + ev.channel;
    return false;
  }
  if (ev.interval_ns > 0 && ev.repeat_end_ns >= ev.sim_time_ns &&
      ev.repeat_end_ns - ev.sim_time_ns >= ev.interval_ns) {
    const uint64_t next_ns = ev.sim_time_ns + ev.interval_ns;
    if (next_ns <= source_->end_ns() && next_ns <= ev.repeat_end_ns) {
      ev.sim_time_ns = next_ns;
      ++ev.sequence;
      message_queue_.Push(std::move(ev));
    }
  }
  return true;
}

bool EmulatorController::WaitAndPublishNext(
    std::chrono::milliseconds feedback_timeout) {
  (void)feedback_timeout;  // Synchronous in-process round, no wall-time pacing.
  return PublishNext();
}

}  // namespace simulation
}  // namespace apollo
