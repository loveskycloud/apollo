/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#include "simulation/worldsim/ego/ego_car.h"

namespace apollo {
namespace simulation {
namespace worldsim {

void EgoCar::Init(const EgoConfig& config) {
  config_ = config;
  has_config_ = true;
  if (config.has_position()) {
    x_ = config.position().x();
    y_ = config.position().y();
    z_ = config.position().z();
  }
  heading_ = config.heading();
  active_route_id_ = config.active_route_id();
  vehicle_profile_ = config.vehicle_profile();
}

void EgoCar::Reset() {
  if (!has_config_) {
    return;
  }
  Init(config_);
}

bool EgoCar::GetRouteEnd(double* x, double* y, double* heading) const {
  const Route* route = nullptr;
  for (const auto& r : config_.routes()) {
    if (r.id() == active_route_id_) {
      route = &r;
      break;
    }
  }
  if (route == nullptr || route->waypoints_size() == 0) {
    return false;
  }
  const auto& wp = route->waypoints(route->waypoints_size() - 1);
  *x = wp.position().x();
  *y = wp.position().y();
  *heading = wp.has_heading() ? wp.heading() : heading_;
  return true;
}

std::vector<Waypoint> EgoCar::GetRoutingWaypoints() const {
  std::vector<Waypoint> out;
  for (const auto& r : config_.routes()) {
    if (r.id() != active_route_id_) {
      continue;
    }
    for (const auto& wp : r.waypoints()) {
      out.push_back(wp);
    }
    break;
  }
  return out;
}

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
