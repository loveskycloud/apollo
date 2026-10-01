/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *
 * Keep algorithm in sync with scene_editor/src/core/bezierPath.ts
 *****************************************************************************/
#include "modules/simulation/worldsim/agent/bezier_path.h"

#include <algorithm>
#include <cmath>

namespace apollo {
namespace simulation {
namespace worldsim {
namespace {

struct Vec2 {
  double x = 0.0;
  double y = 0.0;
};

double Dist(const Vec2& a, const Vec2& b) {
  return std::hypot(b.x - a.x, b.y - a.y);
}

Vec2 EvalCubic(const Vec2& p0, const Vec2& c_out, const Vec2& c_in,
               const Vec2& p1, double t) {
  const double u = 1.0 - t;
  const double uu = u * u;
  const double tt = t * t;
  return {uu * u * p0.x + 3 * uu * t * c_out.x + 3 * u * tt * c_in.x +
              tt * t * p1.x,
          uu * u * p0.y + 3 * uu * t * c_out.y + 3 * u * tt * c_in.y +
              tt * t * p1.y};
}

Vec2 ToVec2(const Vec3& v) { return {v.x(), v.y()}; }

Vec2 DefaultHandleOut(const Vec2& p, const Vec2& next) {
  const double len = Dist(p, next) / 3.0;
  const double L = Dist(p, next);
  if (L < 1e-9) return p;
  return {p.x + (next.x - p.x) / L * len, p.y + (next.y - p.y) / L * len};
}

Vec2 DefaultHandleIn(const Vec2& prev, const Vec2& p) {
  const double len = Dist(prev, p) / 3.0;
  const double L = Dist(prev, p);
  if (L < 1e-9) return p;
  return {p.x - (p.x - prev.x) / L * len, p.y - (p.y - prev.y) / L * len};
}

Vec2 DefaultSmoothIn(const Vec2& prev, const Vec2& p, const Vec2& next) {
  const double dx = next.x - prev.x;
  const double dy = next.y - prev.y;
  const double len_in = Dist(prev, p) / 3.0;
  const double L = std::hypot(dx, dy);
  if (L < 1e-9) return p;
  return {p.x - dx / L * len_in, p.y - dy / L * len_in};
}

Vec2 DefaultSmoothOut(const Vec2& prev, const Vec2& p, const Vec2& next) {
  const double dx = next.x - prev.x;
  const double dy = next.y - prev.y;
  const double len_out = Dist(p, next) / 3.0;
  const double L = std::hypot(dx, dy);
  if (L < 1e-9) return p;
  return {p.x + dx / L * len_out, p.y + dy / L * len_out};
}

}  // namespace

bool RouteIsBezier(const Route& route, AgentType agent_type) {
  if (agent_type == AGENT_TYPE_PEDESTRIAN) {
    return true;
  }
  if (!route.has_path_type()) {
    return false;
  }
  const std::string& t = route.path_type();
  return t == "bezier" || t == "BEZIER";
}

std::vector<ArcSample> BuildBezierArcTable(const Route& route, double step) {
  std::vector<ArcSample> table;
  const int n = route.waypoints_size();
  if (n <= 0) {
    return table;
  }
  if (n == 1) {
    const auto& w = route.waypoints(0);
    table.push_back({w.position().x(), w.position().y(), 0.0,
                     w.has_heading() ? w.heading() : 0.0});
    return table;
  }

  std::vector<Vec2> pts(n);
  std::vector<Vec2> hins(n);
  std::vector<Vec2> houts(n);
  for (int i = 0; i < n; ++i) {
    pts[i] = ToVec2(route.waypoints(i).position());
  }
  for (int i = 0; i < n; ++i) {
    const auto& w = route.waypoints(i);
    if (i > 0 && i + 1 < n) {
      hins[i] = w.has_handle_in() ? ToVec2(w.handle_in())
                                  : DefaultSmoothIn(pts[i - 1], pts[i], pts[i + 1]);
      houts[i] = w.has_handle_out()
                     ? ToVec2(w.handle_out())
                     : DefaultSmoothOut(pts[i - 1], pts[i], pts[i + 1]);
    } else if (i + 1 < n) {
      houts[i] = w.has_handle_out() ? ToVec2(w.handle_out())
                                    : DefaultHandleOut(pts[i], pts[i + 1]);
      hins[i] = w.has_handle_in() ? ToVec2(w.handle_in()) : pts[i];
    } else if (i > 0) {
      hins[i] = w.has_handle_in() ? ToVec2(w.handle_in())
                                  : DefaultHandleIn(pts[i - 1], pts[i]);
      houts[i] = w.has_handle_out() ? ToVec2(w.handle_out()) : pts[i];
    } else {
      hins[i] = pts[i];
      houts[i] = pts[i];
    }
  }

  std::vector<Vec2> dense;
  for (int i = 0; i + 1 < n; ++i) {
    const Vec2& p0 = pts[i];
    const Vec2& p1 = pts[i + 1];
    const Vec2& c_out = houts[i];
    const Vec2& c_in = hins[i + 1];
    const double chord = Dist(p0, p1);
    const int samples = std::max(4, static_cast<int>(std::ceil(chord / step) * 2));
    for (int k = 0; k <= samples; ++k) {
      if (i > 0 && k == 0) {
        continue;
      }
      const double t = static_cast<double>(k) / samples;
      dense.push_back(EvalCubic(p0, c_out, c_in, p1, t));
    }
  }

  double s = 0.0;
  for (size_t i = 0; i < dense.size(); ++i) {
    if (i > 0) {
      s += Dist(dense[i - 1], dense[i]);
    }
    double heading = 0.0;
    if (i + 1 < dense.size()) {
      heading = std::atan2(dense[i + 1].y - dense[i].y,
                           dense[i + 1].x - dense[i].x);
    } else if (i > 0) {
      heading = std::atan2(dense[i].y - dense[i - 1].y,
                           dense[i].x - dense[i - 1].x);
    }
    table.push_back({dense[i].x, dense[i].y, s, heading});
  }
  return table;
}

bool SampleArcTable(const std::vector<ArcSample>& table, double s, double* x,
                    double* y, double* heading) {
  if (table.empty() || x == nullptr || y == nullptr) {
    return false;
  }
  if (table.size() == 1 || s <= table.front().s) {
    *x = table.front().x;
    *y = table.front().y;
    if (heading) {
      *heading = table.front().heading;
    }
    return true;
  }
  if (s >= table.back().s) {
    *x = table.back().x;
    *y = table.back().y;
    if (heading) {
      *heading = table.back().heading;
    }
    return true;
  }
  for (size_t i = 1; i < table.size(); ++i) {
    if (s <= table[i].s) {
      const auto& a = table[i - 1];
      const auto& b = table[i];
      const double ds = b.s - a.s;
      const double u = ds > 1e-9 ? (s - a.s) / ds : 0.0;
      *x = a.x + (b.x - a.x) * u;
      *y = a.y + (b.y - a.y) * u;
      if (heading) {
        *heading = b.heading;
      }
      return true;
    }
  }
  *x = table.back().x;
  *y = table.back().y;
  if (heading) {
    *heading = table.back().heading;
  }
  return true;
}

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
