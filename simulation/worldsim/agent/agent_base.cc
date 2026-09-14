/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#include "simulation/worldsim/agent/agent_base.h"

namespace apollo {
namespace simulation {
namespace worldsim {

AgentBase::AgentBase(const AgentConfig& config) : config_(config) {
  id_ = config.id();
  type_ = config.type();
  state_.id = id_;
  state_.type = type_;
  if (config.has_position()) {
    state_.x = config.position().x();
    state_.y = config.position().y();
    state_.z = config.position().z();
  }
  state_.heading = config.heading();
  state_.speed = config.speed();
  if (config.has_size()) {
    state_.length = config.size().x() > 0.1 ? config.size().x() : 4.5;
    state_.width = config.size().y() > 0.1 ? config.size().y() : 2.0;
    state_.height = config.size().z() > 0.1 ? config.size().z() : 1.5;
  }
  // enabled defaults to true when field absent
  state_.enabled = !config.has_enabled() || config.enabled();
  // Disabled agents hold even if speed > 0
  state_.moving = state_.enabled && state_.speed > 1e-3;
}

void AgentBase::ApplyAction(const BehaviorAction& action) {
  switch (action.kind()) {
    case ACTION_SET_SPEED:
      state_.speed = action.speed();
      state_.enabled = true;
      state_.moving = state_.speed > 1e-3;
      break;
    case ACTION_STOP:
      state_.speed = 0.0;
      state_.moving = false;
      break;
    case ACTION_ENABLE:
      state_.enabled = true;
      if (state_.speed <= 1e-3 && config_.speed() > 1e-3) {
        state_.speed = config_.speed();
      }
      state_.moving = state_.speed > 1e-3;
      break;
    case ACTION_DISABLE:
      state_.enabled = false;
      state_.moving = false;
      break;
    case ACTION_START_ROUTE:
    case ACTION_SWITCH_ROUTE:
      state_.enabled = true;
      if (state_.speed <= 1e-3 && config_.speed() > 1e-3) {
        state_.speed = config_.speed();
      }
      state_.moving = state_.speed > 1e-3;
      break;
    default:
      break;
  }
}

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
