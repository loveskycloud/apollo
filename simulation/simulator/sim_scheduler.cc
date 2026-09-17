/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/sim_scheduler.h"

#include <chrono>
#include <utility>

namespace apollo {
namespace simulation {

SimScheduler* SimScheduler::Instance() {
  static SimScheduler instance;
  return &instance;
}

void SimScheduler::Init(ComputationalGraph* graph) {
  graph_ = graph;
  provider_.SetFeedbackCallback([this]() { SignalFeedback(); });
}

void SimScheduler::RegisterService(
    const std::string& name, std::shared_ptr<ModuleReplayService> service) {
  services_[name] = std::move(service);
}

void SimScheduler::OnMessageReceived(const BufferedMessage& msg,
                                     uint64_t sim_clock_ns) {
  if (graph_ == nullptr) {
    return;
  }
  for (auto& pair : services_) {
    bool ready = false;
    pair.second->OnMessage(msg, sim_clock_ns, &ready);
    if (ready) {
      PrioritizedTask task;
      task.timestamp_ns = msg.timestamp_ns;
      task.priority = graph_->GetPriority(pair.first);
      task.meta.module_name = pair.first;
      task.meta.timestamp_ns = msg.timestamp_ns;
      task.meta.priority = task.priority;
      ready_queue_.Push(task);
    }
  }
  not_ready_pool_.PromoteReady(sim_clock_ns, &ready_queue_);
}

bool SimScheduler::RunOneRound(uint64_t frozen_clock_ns,
                               MessageBuffer* global_buffer) {
  feedback_received_ = false;
  while (!provider_.IsRoundOver(ready_queue_, executor_.IsBusy())) {
    while (provider_.PollAndPackage(&ready_queue_, &packaged_queue_)) {
      while (!packaged_queue_.empty()) {
        executor_.ExecuteOne(&packaged_queue_, global_buffer, *graph_);
      }
    }
    not_ready_pool_.PromoteReady(frozen_clock_ns, &ready_queue_);
    if (provider_.IsRoundOver(ready_queue_, executor_.IsBusy())) {
      break;
    }
  }
  return true;
}

void SimScheduler::SignalFeedback() {
  {
    std::lock_guard<std::mutex> lock(feedback_mutex_);
    feedback_received_ = true;
  }
  feedback_cv_.notify_all();
}

bool SimScheduler::WaitForFeedback(std::chrono::milliseconds timeout) {
  std::unique_lock<std::mutex> lock(feedback_mutex_);
  return feedback_cv_.wait_for(lock, timeout,
                               [this]() { return feedback_received_; });
}

}  // namespace simulation
}  // namespace apollo
