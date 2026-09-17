/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#pragma once

#include <string>
#include <vector>

#include "modules/simulation/worldsim/proto/scenario.pb.h"

namespace apollo {
namespace simulation {
namespace worldsim {

/**
 * Ego is the ADC under test — not a traffic AgentBase.
 * WorldSim initializes pose / route; SimPerfectControl + Apollo PnC drive motion.
 */
class EgoCar {
 public:
  void Init(const EgoConfig& config);
  void Reset();

  const EgoConfig& config() const { return config_; }
  bool has_config() const { return has_config_; }

  double x() const { return x_; }
  double y() const { return y_; }
  double z() const { return z_; }
  double heading() const { return heading_; }
  const std::string& active_route_id() const { return active_route_id_; }
  const std::string& vehicle_profile() const { return vehicle_profile_; }

  /** First destination waypoint of active route, if any. */
  bool GetRouteEnd(double* x, double* y, double* heading) const;
  /** Collect routing waypoints (excluding start pose) for LaneFollow. */
  std::vector<Waypoint> GetRoutingWaypoints() const;
  /** Advance active_route_id to the next declared route; false if none. */
  bool SwitchToNextRoute();

 private:
  EgoConfig config_;
  bool has_config_ = false;
  double x_ = 0.0;
  double y_ = 0.0;
  double z_ = 0.0;
  double heading_ = 0.0;
  std::string active_route_id_;
  std::string vehicle_profile_;
};

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
