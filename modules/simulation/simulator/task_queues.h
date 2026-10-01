/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_TASK_QUEUES_H_
#define SIMULATION_SIMULATOR_TASK_QUEUES_H_

#include <functional>
#include <mutex>
#include <queue>
#include <vector>

#include "modules/simulation/simulator/computational_graph.h"
#include "modules/simulation/simulator/message_buffer.h"
#include "modules/simulation/simulator/sim_event.h"

namespace apollo {
namespace simulation {

struct PrioritizedTask {
  uint64_t timestamp_ns = 0;
  int priority = 0;
  TaskMeta meta;

  bool operator>(const PrioritizedTask& other) const {
    if (timestamp_ns != other.timestamp_ns) {
      return timestamp_ns > other.timestamp_ns;
    }
    return priority > other.priority;
  }
};

class TaskQueueWithPriority {
 public:
  void Push(PrioritizedTask task);
  bool Empty() const;
  bool Pop(PrioritizedTask* out);
  size_t Size() const;

 private:
  mutable std::mutex mutex_;
  std::priority_queue<PrioritizedTask, std::vector<PrioritizedTask>,
                      std::greater<PrioritizedTask>>
      queue_;
};

class NotReadyTaskPool {
 public:
  void Push(PrioritizedTask task);
  void PromoteReady(uint64_t sim_clock_ns, TaskQueueWithPriority* ready_queue);
  bool Empty() const;
  size_t Size() const;

 private:
  mutable std::mutex mutex_;
  std::vector<PrioritizedTask> tasks_;
};

class TaskProvider {
 public:
  using FeedbackCallback = std::function<void()>;

  void SetFeedbackCallback(FeedbackCallback cb) { feedback_cb_ = cb; }
  bool PollAndPackage(TaskQueueWithPriority* ready_queue,
                      std::queue<PrioritizedTask>* packaged);
  bool IsRoundOver(const TaskQueueWithPriority& ready_queue,
                   bool executor_busy) const;

 private:
  FeedbackCallback feedback_cb_;
};

class TaskExecutor {
 public:
  using RunCallback = std::function<void(
      const PrioritizedTask&, std::vector<BufferedMessage>*)>;

  void SetRunCallback(RunCallback cb) { run_cb_ = cb; }
  bool ExecuteOne(std::queue<PrioritizedTask>* packaged,
                  MessageBuffer* global_buffer,
                  const ComputationalGraph& graph);
  bool IsBusy() const { return busy_; }

 private:
  RunCallback run_cb_;
  bool busy_ = false;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_TASK_QUEUES_H_
