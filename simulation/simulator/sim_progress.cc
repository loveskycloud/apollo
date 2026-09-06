/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/sim_progress.h"

#include <fstream>

#include "cyber/time/time.h"

namespace apollo {
namespace simulation {

void SimProgress::Init(const SimProgressState& initial) {
  state_ = initial;
  wall_start_s_ = cyber::Time::Now().ToSecond();
}

void SimProgress::OnEvent(uint64_t sim_time_ns, const std::string& channel) {
  state_.sim_time_s = static_cast<double>(sim_time_ns) / 1e9;
  state_.last_channel = channel;
  ++state_.events_done;
  if (state_.events_total > 0) {
    state_.percent =
        100.0 * static_cast<double>(state_.events_done) / state_.events_total;
  }
  const double wall_now = cyber::Time::Now().ToSecond();
  state_.wall_elapsed_s = wall_now - wall_start_s_;
  if (state_.wall_elapsed_s > 0.0) {
    state_.speedup = state_.sim_time_s / state_.wall_elapsed_s;
  }
}

void SimProgress::WriteJson(const std::string& path) const {
  std::ofstream ofs(path);
  if (!ofs) {
    return;
  }
  ofs << "{\n";
  ofs << "  \"scenario_id\": \"" << state_.scenario_id << "\",\n";
  ofs << "  \"sim_time_s\": " << state_.sim_time_s << ",\n";
  ofs << "  \"percent\": " << state_.percent << ",\n";
  ofs << "  \"events_done\": " << state_.events_done << ",\n";
  ofs << "  \"events_total\": " << state_.events_total << ",\n";
  ofs << "  \"speedup\": " << state_.speedup << "\n";
  ofs << "}\n";
}

}  // namespace simulation
}  // namespace apollo
