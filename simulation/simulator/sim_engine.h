/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_SIM_ENGINE_H_
#define SIMULATION_SIMULATOR_SIM_ENGINE_H_

#include <memory>

#include "simulation/simulator/emulator_controller.h"
#include "simulation/simulator/emulator_message_queue.h"
#include "simulation/simulator/result_sink.h"
#include "simulation/simulator/sim_monitor.h"
#include "simulation/simulator/sim_progress.h"
#include "simulation/simulator/virtual_timer_scheduler.h"

namespace apollo {
namespace simulation {

class SimEngine {
 public:
  struct Options {
    EmulatorController* controller = nullptr;
    FabricatedMessageQueue* fabricated = nullptr;
    VirtualTimerScheduler* timer_scheduler = nullptr;
    ResultSink* result_sink = nullptr;
    SimProgress* progress = nullptr;
    SimMonitor* monitor = nullptr;
    uint64_t begin_ns = 0;
    uint64_t end_ns = 0;
  };

  bool Init(const Options& opts);
  int RunAFAP();

 private:
  EmulatorController* controller_ = nullptr;
  FabricatedMessageQueue* fabricated_ = nullptr;
  VirtualTimerScheduler* timer_scheduler_ = nullptr;
  ResultSink* result_sink_ = nullptr;
  SimProgress* progress_ = nullptr;
  SimMonitor* monitor_ = nullptr;
  uint64_t begin_ns_ = 0;
  uint64_t end_ns_ = 0;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_SIM_ENGINE_H_
