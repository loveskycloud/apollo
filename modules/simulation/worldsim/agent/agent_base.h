/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#pragma once

#include <string>

#include "modules/simulation/worldsim/proto/scenario.pb.h"

namespace apollo {
namespace simulation {
namespace worldsim {

struct AgentState {
  std::string id;
  AgentType type = AGENT_TYPE_UNKNOWN;
  double x = 0.0;
  double y = 0.0;
  double z = 0.0;
  double heading = 0.0;
  double speed = 0.0;
  double length = 4.5;
  double width = 2.0;
  double height = 1.5;
  bool moving = false;
  /** When false, Tick is a no-op until Trigger enable / set_speed. */
  bool enabled = true;
};

class AgentBase {
 public:
  explicit AgentBase(const AgentConfig& config);
  virtual ~AgentBase() = default;

  virtual void Init() {}
  virtual void Tick(double dt) = 0;
  virtual void ApplyAction(const BehaviorAction& action);

  const std::string& id() const { return id_; }
  AgentType type() const { return type_; }
  const AgentState& state() const { return state_; }
  AgentState* mutable_state() { return &state_; }

 protected:
  std::string id_;
  AgentType type_;
  AgentConfig config_;
  AgentState state_;
};

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
