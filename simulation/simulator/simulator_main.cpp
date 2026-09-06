/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include <iostream>
#include <string>

#include "gflags/gflags.h"

#include "simulation/simulator/sim_initializer.h"

DEFINE_string(task_dir, "", "Simulation task directory");

int main(int argc, char** argv) {
  google::ParseCommandLineFlags(&argc, &argv, true);
  if (FLAGS_task_dir.empty()) {
    std::cerr << "Usage: simulator_main --task_dir=/path/to/task\n";
    return 1;
  }
  apollo::simulation::SimInitializer initializer;
  apollo::simulation::SimInitializer::Context ctx;
  if (!initializer.Init(FLAGS_task_dir, &ctx)) {
    std::cerr << "SimInitializer failed\n";
    return 1;
  }
  return initializer.Run(&ctx);
}
