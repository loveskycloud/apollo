/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/task_queues.h"

#include <string>
#include <utility>

namespace apollo {
namespace simulation {

void TaskQueueWithPriority::Push(PrioritizedTask task) {
  std::lock_guard<std::mutex> lock(mutex_);
  queue_.push(std::move(task));
}

bool TaskQueueWithPriority::Empty() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return queue_.empty();
}

bool TaskQueueWithPriority::Pop(PrioritizedTask* out) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!out || queue_.empty()) {
    return false;
  }
  *out = queue_.top();
  queue_.pop();
  return true;
}

size_t TaskQueueWithPriority::Size() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return queue_.size();
}

void NotReadyTaskPool::Push(PrioritizedTask task) {
  std::lock_guard<std::mutex> lock(mutex_);
  tasks_.push_back(std::move(task));
}

void NotReadyTaskPool::PromoteReady(uint64_t sim_clock_ns,
                                    TaskQueueWithPriority* ready_queue) {
  std::lock_guard<std::mutex> lock(mutex_);
  std::vector<PrioritizedTask> remain;
  for (auto& task : tasks_) {
    if (task.timestamp_ns <= sim_clock_ns) {
      ready_queue->Push(task);
    } else {
      remain.push_back(std::move(task));
    }
  }
  tasks_.swap(remain);
}

bool NotReadyTaskPool::Empty() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return tasks_.empty();
}

size_t NotReadyTaskPool::Size() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return tasks_.size();
}

bool TaskProvider::PollAndPackage(TaskQueueWithPriority* ready_queue,
                                  std::queue<PrioritizedTask>* packaged) {
  if (!ready_queue || !packaged) {
    return false;
  }
  PrioritizedTask task;
  if (!ready_queue->Pop(&task)) {
    return false;
  }
  packaged->push(task);
  if (feedback_cb_) {
    feedback_cb_();
  }
  return true;
}

bool TaskProvider::IsRoundOver(const TaskQueueWithPriority& ready_queue,
                               bool executor_busy) const {
  return !executor_busy && ready_queue.Empty();
}

bool TaskExecutor::ExecuteOne(std::queue<PrioritizedTask>* packaged,
                              MessageBuffer* global_buffer,
                              const ComputationalGraph& graph) {
  if (!packaged || packaged->empty() || !global_buffer) {
    return false;
  }
  busy_ = true;
  auto task = packaged->front();
  packaged->pop();
  const auto* spec = graph.GetSpec(task.meta.module_name);
  std::vector<std::string> channels;
  if (spec != nullptr) {
  for (int i = 0; i < spec->prerequisite_channels_size(); ++i) {
      channels.push_back(spec->prerequisite_channels(i));
    }
    if (!spec->trigger_channel().empty()) {
      channels.push_back(spec->trigger_channel());
    }
    if (!spec->periodic_trigger_channel().empty()) {
      channels.push_back(spec->periodic_trigger_channel());
    }
  }
  auto picked = global_buffer->PickRequiredDefault(task.meta.module_name,
                                                   task.timestamp_ns, channels);
  if (run_cb_) {
    run_cb_(task, &picked);
  }
  busy_ = false;
  return true;
}

}  // namespace simulation
}  // namespace apollo
