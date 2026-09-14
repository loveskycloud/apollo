/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#include "simulation/worldsim/agent/agents.h"

#include <cmath>
#include <memory>

namespace apollo {
namespace simulation {
namespace worldsim {
namespace {

bool AdvanceAlongRoute(AgentState* state, const AgentConfig& config,
                       int* waypoint_index, double dt) {
  if (!state->moving || state->speed <= 1e-3) {
    return false;
  }
  const Route* route = nullptr;
  for (const auto& r : config.routes()) {
    if (r.id() == config.active_route_id()) {
      route = &r;
      break;
    }
  }
  if (route == nullptr || route->waypoints_size() < 2) {
    state->x += state->speed * std::cos(state->heading) * dt;
    state->y += state->speed * std::sin(state->heading) * dt;
    return true;
  }
  if (*waypoint_index >= route->waypoints_size() - 1) {
    state->speed = 0.0;
    state->moving = false;
    return false;
  }
  const auto& wp = route->waypoints(*waypoint_index + 1);
  const double tx = wp.position().x();
  const double ty = wp.position().y();
  const double dx = tx - state->x;
  const double dy = ty - state->y;
  const double dist = std::hypot(dx, dy);
  if (dist < 0.3) {
    ++(*waypoint_index);
    return true;
  }
  state->heading = std::atan2(dy, dx);
  const double step = std::min(state->speed * dt, dist);
  state->x += step * std::cos(state->heading);
  state->y += step * std::sin(state->heading);
  return true;
}

}  // namespace

PedestrianAgent::PedestrianAgent(const AgentConfig& config)
    : AgentBase(config) {
  // 尊重场景里配置的 speed（含 0）；不再把 0 偷偷改成默认步行速
  state_.speed = config.speed();
  state_.enabled = !config.has_enabled() || config.enabled();
  state_.moving = state_.enabled && state_.speed > 1e-3;
  RebuildPath(/*snap_to_start=*/false);
}

const Route* PedestrianAgent::ActiveRoute() const {
  for (const auto& r : config_.routes()) {
    if (r.id() == config_.active_route_id()) {
      return &r;
    }
  }
  if (config_.routes_size() > 0) {
    return &config_.routes(0);
  }
  return nullptr;
}

void PedestrianAgent::RebuildPath(bool snap_to_start) {
  arc_table_.clear();
  path_ready_ = false;
  s_along_ = 0.0;
  waypoint_index_ = 0;
  const Route* route = ActiveRoute();
  if (route == nullptr || route->waypoints_size() < 2) {
    return;
  }
  if (!RouteIsBezier(*route, config_.type())) {
    return;
  }
  arc_table_ = BuildBezierArcTable(*route, 0.15);
  path_ready_ = arc_table_.size() >= 2;
  if (path_ready_ && snap_to_start) {
    s_along_ = 0.0;
    state_.x = arc_table_.front().x;
    state_.y = arc_table_.front().y;
    state_.heading = arc_table_.front().heading;
  }
}

void PedestrianAgent::ApplyAction(const BehaviorAction& action) {
  switch (action.kind()) {
    case ACTION_SET_SPEED:
      // 允许设为 0（停下）；不再把 0 改成默认步行速
      state_.speed = std::max(0.0, action.speed());
      state_.enabled = true;
      state_.moving = state_.speed > 1e-3;
      if (state_.moving && !path_ready_) {
        RebuildPath(/*snap_to_start=*/false);
      }
      break;
    case ACTION_STOP:
      state_.speed = 0.0;
      state_.moving = false;
      break;
    case ACTION_DISABLE:
      state_.enabled = false;
      state_.moving = false;
      break;
    case ACTION_ENABLE: {
      state_.enabled = true;
      // 用当前/配置速度；配置为 0 则激活后仍静止，等 set_speed
      if (state_.speed <= 1e-3 && config_.speed() > 1e-3) {
        state_.speed = config_.speed();
      }
      state_.moving = state_.speed > 1e-3;
      if (state_.moving && !path_ready_) {
        RebuildPath(/*snap_to_start=*/false);
      }
      break;
    }
    case ACTION_START_ROUTE:
    case ACTION_SWITCH_ROUTE: {
      if (action.has_route_id() && !action.route_id().empty()) {
        config_.set_active_route_id(action.route_id());
      }
      if (state_.speed <= 1e-3 && config_.speed() > 1e-3) {
        state_.speed = config_.speed();
      }
      state_.enabled = true;
      state_.moving = state_.speed > 1e-3;
      RebuildPath(/*snap_to_start=*/true);
      waypoint_index_ = 0;
      break;
    }
    default:
      AgentBase::ApplyAction(action);
      break;
  }
}

void PedestrianAgent::Tick(double dt) {
  if (!state_.enabled || !state_.moving || state_.speed <= 1e-3) {
    return;
  }

  const Route* route = ActiveRoute();
  if (route != nullptr && RouteIsBezier(*route, config_.type())) {
    if (!path_ready_) {
      RebuildPath(/*snap_to_start=*/false);
    }
    if (!path_ready_) {
      return;
    }
    const double total = arc_table_.back().s;
    s_along_ += state_.speed * dt;
    if (s_along_ >= total) {
      s_along_ = total;
      SampleArcTable(arc_table_, s_along_, &state_.x, &state_.y, &state_.heading);
      state_.speed = 0.0;
      state_.moving = false;
      return;
    }
    SampleArcTable(arc_table_, s_along_, &state_.x, &state_.y, &state_.heading);
    return;
  }

  AdvanceAlongRoute(&state_, config_, &waypoint_index_, dt);
}

void VehicleAgent::Tick(double dt) {
  if (!state_.enabled) {
    return;
  }
  AdvanceAlongRoute(&state_, config_, &waypoint_index_, dt);
}

BicycleAgent::BicycleAgent(const AgentConfig& config) : VehicleAgent(config) {
  if (!config.has_size()) {
    state_.length = 1.8;
    state_.width = 0.6;
    state_.height = 1.5;
  }
}

TruckAgent::TruckAgent(const AgentConfig& config) : VehicleAgent(config) {
  if (!config.has_size()) {
    state_.length = 8.0;
    state_.width = 2.5;
    state_.height = 3.2;
  }
}

IdmAgent::IdmAgent(const AgentConfig& config) : AgentBase(config) {
  if (config.desired_speed() > 0) {
    desired_speed_ = config.desired_speed();
  } else if (config.speed() > 0) {
    desired_speed_ = config.speed();
  }
  if (config.min_gap() > 0) {
    min_gap_ = config.min_gap();
  }
  if (config.time_headway() > 0) {
    time_headway_ = config.time_headway();
  }
  if (config.max_accel() > 0) {
    max_accel_ = config.max_accel();
  }
  if (config.comfortable_decel() > 0) {
    comfort_decel_ = config.comfortable_decel();
  }
  state_.moving = true;
  if (state_.speed <= 1e-3) {
    state_.speed = desired_speed_;
  }
}

void IdmAgent::Tick(double dt) {
  if (!state_.enabled) {
    return;
  }
  // Free-road IDM: a = a_max * (1 - (v/v0)^4)
  const double v = state_.speed;
  const double ratio = desired_speed_ > 1e-3 ? v / desired_speed_ : 1.0;
  const double accel =
      max_accel_ * (1.0 - std::pow(std::min(ratio, 2.0), 4.0));
  state_.speed = std::max(0.0, v + accel * dt);
  state_.moving = state_.speed > 1e-3;
  AdvanceAlongRoute(&state_, config_, &waypoint_index_, dt);
}

std::unique_ptr<AgentBase> CreateAgent(const AgentConfig& config) {
  switch (config.type()) {
    case AGENT_TYPE_STATIC:
    case AGENT_TYPE_LOADER:
      return std::make_unique<StaticAgent>(config);
    case AGENT_TYPE_PEDESTRIAN:
      return std::make_unique<PedestrianAgent>(config);
    case AGENT_TYPE_BICYCLE:
      return std::make_unique<BicycleAgent>(config);
    case AGENT_TYPE_TRUCK:
      return std::make_unique<TruckAgent>(config);
    case AGENT_TYPE_IDM:
      return std::make_unique<IdmAgent>(config);
    case AGENT_TYPE_VEHICLE:
    default:
      return std::make_unique<VehicleAgent>(config);
  }
}

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
