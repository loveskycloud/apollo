/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#pragma once

#include <memory>
#include <vector>

#include "modules/simulation/worldsim/agent/agent_base.h"
#include "modules/simulation/worldsim/agent/bezier_path.h"

namespace apollo {
namespace simulation {
namespace worldsim {

/** Stationary obstacle; Tick is a no-op. */
class StaticAgent final : public AgentBase {
 public:
  using AgentBase::AgentBase;
  void Tick(double /*dt*/) override {}
};

/**
 * Pedestrian: cubic Bezier free-space path when route.path_type=bezier
 * (default for pedestrians); otherwise polyline AdvanceAlongRoute.
 */
class PedestrianAgent final : public AgentBase {
 public:
  explicit PedestrianAgent(const AgentConfig& config);
  void Tick(double dt) override;
  void ApplyAction(const BehaviorAction& action) override;

 private:
  void RebuildPath(bool snap_to_start = true);
  const Route* ActiveRoute() const;

  int waypoint_index_ = 0;
  double s_along_ = 0.0;
  std::vector<ArcSample> arc_table_;
  bool path_ready_ = false;
};

/** Generic vehicle: follow active route waypoints if present, else cruise. */
class VehicleAgent : public AgentBase {
 public:
  using AgentBase::AgentBase;
  void Tick(double dt) override;

 protected:
  int waypoint_index_ = 0;
};

/** Bicycle: same kinematics as vehicle with smaller default footprint. */
class BicycleAgent final : public VehicleAgent {
 public:
  explicit BicycleAgent(const AgentConfig& config);
};

/** Truck: vehicle with larger footprint defaults. */
class TruckAgent final : public VehicleAgent {
 public:
  explicit TruckAgent(const AgentConfig& config);
};

/**
 * IDM longitudinal model on the active route (simplified).
 * Falls back to constant speed when no route is set.
 */
class IdmAgent final : public AgentBase {
 public:
  explicit IdmAgent(const AgentConfig& config);
  void Tick(double dt) override;

 private:
  double desired_speed_ = 5.0;
  double min_gap_ = 2.0;
  double time_headway_ = 1.5;
  double max_accel_ = 1.5;
  double comfort_decel_ = 2.0;
  int waypoint_index_ = 0;
};

std::unique_ptr<AgentBase> CreateAgent(const AgentConfig& config);

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
