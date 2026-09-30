/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#include "modules/simulation/worldsim/agent/agent_base.h"

#include <cmath>

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
    // Thin objects from perception are valid geometry. Replacing them with
    // car-sized defaults creates obstacles and collisions absent in the scene.
    const auto dimension = [](double value, double fallback) {
      return std::isfinite(value) && value > 0.0 ? value : fallback;
    };
    state_.length = dimension(config.size().x(), state_.length);
    state_.width = dimension(config.size().y(), state_.width);
    state_.height = dimension(config.size().z(), state_.height);
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
