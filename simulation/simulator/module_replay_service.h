/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_MODULE_REPLAY_SERVICE_H_
#define SIMULATION_SIMULATOR_MODULE_REPLAY_SERVICE_H_

#include <string>
#include <vector>

#include "simulation/simulator/computational_graph.h"
#include "simulation/simulator/message_buffer.h"
#include "simulation/simulator/proto/module_trigger.pb.h"

namespace apollo {
namespace simulation {

enum class TaskReadiness { NOT_READY, READY };

class ModuleReplayService {
 public:
  explicit ModuleReplayService(const std::string& module_name);

  void SetSpec(const simulator::ModuleTriggerSpec& spec);
  void OnMessage(const BufferedMessage& msg, uint64_t sim_clock_ns,
                 bool* ready_to_trigger);
  TaskReadiness GetReadiness() const { return readiness_; }
  void MarkTriggered();
  const std::string& module_name() const { return module_name_; }
  MessageBuffer* buffer() { return &buffer_; }

 private:
  bool IsTriggerMessage(const std::string& channel) const;
  bool IsAlignedMode() const { return false; }

  std::string module_name_;
  simulator::ModuleTriggerSpec spec_;
  MessageBuffer buffer_;
  TaskReadiness readiness_ = TaskReadiness::NOT_READY;
  uint64_t pending_trigger_time_ns_ = 0;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_MODULE_REPLAY_SERVICE_H_
