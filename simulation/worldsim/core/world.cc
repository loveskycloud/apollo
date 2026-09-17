/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#include "modules/simulation/worldsim/core/world.h"

#include "cyber/common/log.h"
#include "modules/simulation/worldsim/agent/agents.h"

namespace apollo {
namespace simulation {
namespace worldsim {

bool World::Load(const Scenario& scenario) {
  scenario_ = scenario;
  agents_.clear();
  triggers_.clear();
  sim_time_ = 0.0;
  running_ = false;

  if (scenario.has_ego()) {
    ego_.Init(scenario.ego());
  } else {
    // Fallback: first AGENT_TYPE_EGO in agents list.
    for (const auto& a : scenario.agents()) {
      if (a.type() == AGENT_TYPE_EGO) {
        EgoConfig ego;
        ego.set_id(a.id());
        ego.set_name(a.name());
        if (a.has_position()) {
          *ego.mutable_position() = a.position();
        }
        ego.set_heading(a.heading());
        if (a.has_size()) {
          *ego.mutable_size() = a.size();
        }
        ego.set_active_route_id(a.active_route_id());
        for (const auto& r : a.routes()) {
          *ego.add_routes() = r;
        }
        ego_.Init(ego);
        break;
      }
    }
  }
  if (ego_.has_config()) {
    UpdateEgoPose(ego_.x(), ego_.y(), ego_.z(), ego_.heading(), 0.0);
    ego_state_.id = ego_.config().id().empty() ? "ego" : ego_.config().id();
    ego_state_.type = AGENT_TYPE_EGO;
  }

  for (const auto& a : scenario.agents()) {
    if (a.type() == AGENT_TYPE_EGO) {
      continue;
    }
    auto agent = CreateAgent(a);
    if (agent) {
      agent->Init();
      agents_.push_back(std::move(agent));
    }
  }

  for (const auto& t : scenario.triggers()) {
    auto trigger = CreateTrigger(t);
    if (trigger) {
      trigger->Init(this);
      triggers_.push_back(std::move(trigger));
    }
  }

  AINFO << "World loaded scenario=" << scenario.name()
        << " agents=" << agents_.size()
        << " triggers=" << triggers_.size()
        << " ego=" << (ego_.has_config() ? "yes" : "no");
  // Ego is required for PnC closed-loop, but agent ticking works without it.
  // Do not fail Load when ego is missing — editor may place ego later / separately.
  if (!ego_.has_config()) {
    AWARN << "World loaded without Ego; agent Tick still active, PnC needs Ego";
  }
  return true;
}

void World::Reset() {
  sim_time_ = 0.0;
  running_ = false;
  ego_.Reset();
  // Reload agents/triggers from scenario snapshot.
  Load(scenario_);
}

void World::Tick(double dt) {
  AdvanceTo(sim_time_ + dt);
}

void World::AdvanceTo(double sim_time) {
  if (!running_) {
    return;
  }
  const double dt = sim_time - sim_time_;
  sim_time_ = sim_time;
  for (auto& trigger : triggers_) {
    trigger->Evaluate(sim_time_);
  }
  for (auto& agent : agents_) {
    agent->Tick(dt);
  }
}

const AgentState* World::FindAgentState(const std::string& id) const {
  if (ego_.has_config() &&
      (id.empty() || id == "ego" || id == ego_state_.id)) {
    return &ego_state_;
  }
  for (const auto& agent : agents_) {
    if (agent->id() == id) {
      return &agent->state();
    }
  }
  return nullptr;
}

void World::UpdateEgoPose(double x, double y, double z, double heading,
                          double speed) {
  ego_state_.x = x;
  ego_state_.y = y;
  ego_state_.z = z;
  ego_state_.heading = heading;
  ego_state_.speed = speed;
  ego_state_.moving = speed > 1e-3;
  ego_state_.type = AGENT_TYPE_EGO;
  if (ego_state_.id.empty()) {
    ego_state_.id = "ego";
  }
}

AgentBase* World::FindAgent(const std::string& id) {
  for (auto& agent : agents_) {
    if (agent->id() == id) {
      return agent.get();
    }
  }
  return nullptr;
}

void World::ApplyAction(const BehaviorAction& action) {
  const std::string& target = action.target_agent_id();
  if (target.empty() || target == "ego" ||
      (ego_.has_config() && target == ego_.config().id())) {
    AINFO << "Ego action kind=" << static_cast<int>(action.kind())
          << " (handled by EgoCar / bridge, not AgentBase)";
    return;
  }
  auto* agent = FindAgent(target);
  if (agent != nullptr) {
    agent->ApplyAction(action);
  }
}

std::vector<AgentState> World::SnapshotAgents() const {
  std::vector<AgentState> out;
  out.reserve(agents_.size());
  for (const auto& agent : agents_) {
    out.push_back(agent->state());
  }
  return out;
}

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
