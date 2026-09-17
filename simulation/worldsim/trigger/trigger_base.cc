/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#include "modules/simulation/worldsim/trigger/trigger_base.h"

#include <cmath>
#include <memory>

#include "modules/simulation/worldsim/core/world.h"

namespace apollo {
namespace simulation {
namespace worldsim {

TriggerBase::TriggerBase(const TriggerConfig& config) : config_(config) {
  id_ = config.id();
}

void TriggerBase::Init(World* world) { world_ = world; }

void TriggerBase::Fire() {
  if (fired_ || world_ == nullptr) {
    return;
  }
  fired_ = true;
  for (const auto& action : config_.actions()) {
    world_->ApplyAction(action);
  }
}

bool TimeTrigger::Evaluate(double sim_time) {
  if (fired_) {
    return false;
  }
  if (sim_time + 1e-6 >= config_.time()) {
    Fire();
    return true;
  }
  return false;
}

bool LocationTrigger::Evaluate(double /*sim_time*/) {
  if (fired_ || world_ == nullptr) {
    return false;
  }
  const auto* agent = world_->FindAgentState(config_.target_agent_id());
  if (agent == nullptr) {
    return false;
  }
  const double cx = config_.center().x();
  const double cy = config_.center().y();
  if (config_.has_size() && config_.size().x() > 0 && config_.size().y() > 0) {
    // Oriented box in XY (same as scene_editor playback.ts)
    const double hx = config_.size().x() * 0.5;
    const double hy = config_.size().y() * 0.5;
    const double heading = config_.has_heading() ? config_.heading() : 0.0;
    const double dx = agent->x - cx;
    const double dy = agent->y - cy;
    const double c = std::cos(-heading);
    const double s = std::sin(-heading);
    const double local_x = dx * c - dy * s;
    const double local_y = dx * s + dy * c;
    if (std::fabs(local_x) <= hx && std::fabs(local_y) <= hy) {
      Fire();
      return true;
    }
  } else {
    const double r = config_.radius() > 0 ? config_.radius() : 2.0;
    if (std::hypot(agent->x - cx, agent->y - cy) <= r) {
      Fire();
      return true;
    }
  }
  return false;
}

bool AgentDistanceTrigger::Evaluate(double /*sim_time*/) {
  if (fired_ || world_ == nullptr) {
    return false;
  }
  const auto* a = world_->FindAgentState(config_.agent_a_id());
  const auto* b = world_->FindAgentState(config_.agent_b_id());
  if (a == nullptr || b == nullptr) {
    return false;
  }
  const double d = std::hypot(a->x - b->x, a->y - b->y);
  const bool less = config_.compare() != "greater";
  const bool hit = less ? (d < config_.distance()) : (d > config_.distance());
  if (hit) {
    Fire();
    return true;
  }
  return false;
}

bool SpeedTrigger::Evaluate(double /*sim_time*/) {
  if (fired_ || world_ == nullptr) {
    return false;
  }
  const auto* agent = world_->FindAgentState(config_.target_agent_id());
  if (agent == nullptr) {
    return false;
  }
  const bool less = config_.compare() != "greater";
  const bool hit =
      less ? (agent->speed < config_.speed()) : (agent->speed > config_.speed());
  if (hit) {
    Fire();
    return true;
  }
  return false;
}

bool BehaviorTrigger::Evaluate(double /*sim_time*/) {
  if (fired_ || world_ == nullptr) {
    return false;
  }
  const auto* agent = world_->FindAgentState(config_.source_agent_id());
  if (agent == nullptr) {
    return false;
  }
  const std::string& event = config_.event();
  bool hit = false;
  if (event == "stopped") {
    hit = !agent->moving || agent->speed < 0.1;
  } else if (event == "started") {
    hit = agent->moving && agent->speed > 0.1;
  } else if (event == "arrived") {
    hit = !agent->moving && agent->speed < 0.1;
  }
  if (hit) {
    Fire();
    return true;
  }
  return false;
}

std::unique_ptr<TriggerBase> CreateTrigger(const TriggerConfig& config) {
  switch (config.type()) {
    case TRIGGER_TYPE_TIME:
      return std::make_unique<TimeTrigger>(config);
    case TRIGGER_TYPE_LOCATION:
      return std::make_unique<LocationTrigger>(config);
    case TRIGGER_TYPE_AGENT_DISTANCE:
      return std::make_unique<AgentDistanceTrigger>(config);
    case TRIGGER_TYPE_SPEED:
      return std::make_unique<SpeedTrigger>(config);
    case TRIGGER_TYPE_BEHAVIOR:
      return std::make_unique<BehaviorTrigger>(config);
    default:
      return nullptr;
  }
}

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
