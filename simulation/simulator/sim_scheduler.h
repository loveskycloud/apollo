/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_SIM_SCHEDULER_H_
#define SIMULATION_SIMULATOR_SIM_SCHEDULER_H_

#include <condition_variable>
#include <map>
#include <memory>
#include <mutex>
#include <queue>
#include <string>

#include "simulation/simulator/computational_graph.h"
#include "simulation/simulator/module_replay_service.h"
#include "simulation/simulator/task_queues.h"

namespace apollo {
namespace simulation {

class SimScheduler {
 public:
  static SimScheduler* Instance();

  void Init(ComputationalGraph* graph);
  void RegisterService(const std::string& name,
                       std::shared_ptr<ModuleReplayService> service);
  void OnMessageReceived(const BufferedMessage& msg, uint64_t sim_clock_ns);
  bool RunOneRound(uint64_t frozen_clock_ns, MessageBuffer* global_buffer);
  void SignalFeedback();
  bool WaitForFeedback(std::chrono::milliseconds timeout);

 private:
  SimScheduler() = default;

  ComputationalGraph* graph_ = nullptr;
  std::map<std::string, std::shared_ptr<ModuleReplayService>> services_;
  NotReadyTaskPool not_ready_pool_;
  TaskQueueWithPriority ready_queue_;
  TaskProvider provider_;
  TaskExecutor executor_;
  std::queue<PrioritizedTask> packaged_queue_;
  std::mutex feedback_mutex_;
  std::condition_variable feedback_cv_;
  bool feedback_received_ = false;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_SIM_SCHEDULER_H_
