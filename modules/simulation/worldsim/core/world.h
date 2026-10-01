/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#pragma once

#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include "modules/simulation/worldsim/agent/agent_base.h"
#include "modules/simulation/worldsim/ego/ego_car.h"
#include "modules/simulation/worldsim/proto/scenario.pb.h"
#include "modules/simulation/worldsim/trigger/trigger_base.h"

namespace apollo {
namespace simulation {
namespace worldsim {

class World {
 public:
  bool Load(const Scenario& scenario);
  void Reset();
  void Tick(double dt);
  void AdvanceTo(double sim_time);

  double sim_time() const { return sim_time_; }
  bool running() const { return running_; }
  void set_running(bool v) { running_ = v; }

  const Scenario& scenario() const { return scenario_; }
  EgoCar* ego() { return &ego_; }
  const EgoCar* ego() const { return &ego_; }

  const std::vector<std::unique_ptr<AgentBase>>& agents() const {
    return agents_;
  }
  const AgentState* FindAgentState(const std::string& id) const;
  AgentBase* FindAgent(const std::string& id);

  void ApplyAction(const BehaviorAction& action);
  void UpdateEgoPose(double x, double y, double z, double heading, double speed);

  /** Snapshot non-ego agents for gt_obstacles publish. */
  std::vector<AgentState> SnapshotAgents() const;

 private:
  Scenario scenario_;
  EgoCar ego_;
  AgentState ego_state_;
  std::vector<std::unique_ptr<AgentBase>> agents_;
  std::vector<std::unique_ptr<TriggerBase>> triggers_;
  double sim_time_ = 0.0;
  bool running_ = false;
};

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
