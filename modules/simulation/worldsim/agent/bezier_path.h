/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *
 * Keep algorithm in sync with scene_editor/src/core/bezierPath.ts
 *****************************************************************************/
#pragma once

#include <string>
#include <vector>

#include "modules/simulation/worldsim/proto/scenario.pb.h"

namespace apollo {
namespace simulation {
namespace worldsim {

struct ArcSample {
  double x = 0.0;
  double y = 0.0;
  double s = 0.0;
  double heading = 0.0;
};

/** True when route should follow cubic Bezier (pedestrian free-space). */
bool RouteIsBezier(const Route& route, AgentType agent_type);

/**
 * Build dense arc-length table from Route waypoints (+ handle_in/out).
 * Missing handles fall back to 1/3 chord defaults (same as frontend).
 */
std::vector<ArcSample> BuildBezierArcTable(const Route& route,
                                           double step = 0.15);

/** Lookup pose at arc length s (clamped). Returns false if table empty. */
bool SampleArcTable(const std::vector<ArcSample>& table, double s, double* x,
                    double* y, double* heading);

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
