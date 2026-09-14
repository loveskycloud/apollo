/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_SIM_INITIALIZER_H_
#define SIMULATION_SIMULATOR_SIM_INITIALIZER_H_

#include <memory>
#include <string>

#include "cyber/node/node.h"
#include "simulation/logsim/proto/simulation_task.pb.h"
#include "simulation/simulator/emulator_controller.h"
#include "simulation/simulator/message_consumer.h"
#include "simulation/simulator/output_channel_recorder.h"
#include "simulation/simulator/result_sink.h"
#include "simulation/simulator/sim_engine.h"
#include "simulation/simulator/sim_monitor.h"
#include "simulation/simulator/sim_progress.h"

namespace apollo {
namespace simulation {

class SimInitializer {
 public:
  struct Context {
    logsim::SimulationTask task;
    std::shared_ptr<cyber::Node> node;
    MessageConsumer consumer;
    EmulatorController controller;
    FabricatedMessageQueue fabricated;
    VirtualTimerScheduler timer_scheduler;
    ResultSink result_sink;
    OutputChannelRecorder output_recorder;
    SimProgress progress;
    SimMonitor monitor;
    SimEngine engine;
  };

  bool Init(const std::string& task_dir, Context* ctx);
  int Run(Context* ctx);

 private:
  bool LoadTask(const std::string& task_dir, logsim::SimulationTask* task);
  bool SetupCyber(const logsim::SimulationTask& task);
  bool Warmup(const logsim::SimulationTask& task);
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_SIM_INITIALIZER_H_
