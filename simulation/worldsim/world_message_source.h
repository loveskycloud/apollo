#pragma once

#include <memory>
#include <string>
#include <vector>

#include "cyber/node/node.h"
#include "modules/common_msgs/control_msgs/control_cmd.pb.h"
#include "modules/common_msgs/planning_msgs/planning.pb.h"
#include "simulation/simulator/i_message_source.h"
#include "simulation/worldsim/core/world.h"

namespace apollo {
namespace simulation {

// Lazy closed-loop input adapter. No wall-clock timer, module loader or separate
// transport: each step runs in the exact same scheduler as RecordFileSource.
class WorldMessageSource final : public IMessageSource {
 public:
  bool Open(const SourceConfig& cfg) override;
  bool HasNext() const override { return next_ns_ <= end_ns_; }
  bool Peek(SimEvent* out) const override;
  bool Next(SimEvent* out) override;
  uint64_t begin_ns() const override { return begin_ns_; }
  uint64_t end_ns() const override { return end_ns_; }
  uint64_t total_messages() const override { return (end_ns_ - begin_ns_) / step_ns_ + 1; }

 private:
  bool Step(uint64_t now_ns);
  bool AdvanceEgo(uint64_t now_ns);
  bool SendRoute(uint64_t now_ns);

  SourceConfig config_;
  worldsim::World world_;
  std::shared_ptr<cyber::Node> node_;
  std::vector<std::shared_ptr<cyber::ReaderBase>> readers_;
  std::shared_ptr<planning::ADCTrajectory> planning_;
  std::shared_ptr<control::ControlCommand> control_;
  uint64_t begin_ns_ = 0, end_ns_ = 0, next_ns_ = 0, step_ns_ = 0;
  uint64_t last_ns_ = 0;
  double x_ = 0, y_ = 0, z_ = 0, heading_ = 0, speed_ = 0;
  double acceleration_ = 0, steering_ = 0;
  bool route_ready_ = false;
  bool callback_failed_ = false;
  bool control_ready_ = false;
};

}  // namespace simulation
}  // namespace apollo
