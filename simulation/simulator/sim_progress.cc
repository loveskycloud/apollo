/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/sim_progress.h"

#include <fstream>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <iomanip>
#include <stdexcept>

#include "cyber/time/time.h"

namespace apollo {
namespace simulation {

void SimProgress::Init(const SimProgressState& initial) {
  state_ = initial;
  wall_start_s_ = std::chrono::duration<double>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
}

void SimProgress::OnEvent(uint64_t sim_time_ns, const std::string& channel) {
  state_.sim_time_s = static_cast<double>(sim_time_ns) / 1e9;
  state_.last_channel = channel;
  ++state_.events_done;
  if (state_.events_total > 0) {
    state_.percent =
        100.0 * static_cast<double>(state_.events_done) / state_.events_total;
  } else if (state_.end_s > state_.begin_s) {
    state_.percent = std::clamp(100.0 * (state_.sim_time_s - state_.begin_s) /
                                   (state_.end_s - state_.begin_s), 0.0, 100.0);
  }
  const double wall_now = std::chrono::duration<double>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
  state_.wall_elapsed_s = wall_now - wall_start_s_;
  if (state_.wall_elapsed_s > 0.0) {
    state_.speedup = (state_.sim_time_s - state_.begin_s) / state_.wall_elapsed_s;
  }
}

void SimProgress::WriteJson(const std::string& path) const {
  std::ofstream ofs(path + ".tmp");
  if (!ofs) {
    throw std::runtime_error("Cannot write simulation progress: " + path);
  }
  ofs << "{\n";
  ofs << std::setprecision(17);
  ofs << "  \"sim_time_s\": " << state_.sim_time_s << ",\n";
  ofs << "  \"percent\": " << state_.percent << ",\n";
  ofs << "  \"events_done\": " << state_.events_done << ",\n";
  ofs << "  \"events_total\": " << state_.events_total << ",\n";
  ofs << "  \"speedup\": " << state_.speedup << "\n";
  ofs << "}\n";
  ofs.close();
  if (!ofs || std::rename((path + ".tmp").c_str(), path.c_str()) != 0) {
    throw std::runtime_error("Failed to publish simulation progress: " + path);
  }
}

}  // namespace simulation
}  // namespace apollo
