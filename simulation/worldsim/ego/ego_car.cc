/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#include "modules/simulation/worldsim/ego/ego_car.h"

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

bool EgoCar::SwitchToNextRoute() {
  int idx = -1;
  for (int i = 0; i < config_.routes_size(); ++i) {
    if (config_.routes(i).id() == active_route_id_) {
      idx = i;
      break;
    }
  }
  if (idx < 0 || idx + 1 >= config_.routes_size()) {
    return false;
  }
  active_route_id_ = config_.routes(idx + 1).id();
  config_.set_active_route_id(active_route_id_);
  return true;
}

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
