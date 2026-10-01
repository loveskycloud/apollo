/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_SIM_PROGRESS_H_
#define SIMULATION_SIMULATOR_SIM_PROGRESS_H_

#include <cstdint>
#include <string>

namespace apollo {
namespace simulation {

struct SimProgressState {
  std::string scenario_id;
  double sim_time_s = 0.0;
  double begin_s = 0.0;
  double end_s = 0.0;
  double percent = 0.0;
  uint64_t events_done = 0;
  uint64_t events_total = 0;
  std::string last_channel;
  std::string last_module_fired;
  double wall_elapsed_s = 0.0;
  double speedup = 0.0;
};

class SimProgress {
 public:
  void Init(const SimProgressState& initial);
  void OnEvent(uint64_t sim_time_ns, const std::string& channel);
  /** Mark a clean run finish (EOF / mission stop) as 100% progress. */
  void MarkComplete();
  const SimProgressState& state() const { return state_; }
  void WriteJson(const std::string& path) const;

 private:
  SimProgressState state_;
  double wall_start_s_ = 0.0;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_SIM_PROGRESS_H_
