#pragma once

#include <cmath>
#include <initializer_list>

namespace apollo::simulation::worldsim {
// Physical oriented rectangles. Touching counts as contact; no planning margin.
struct BodyBox {
  double x, y, heading, half_length, half_width;
};
inline bool BodiesCollide(const BodyBox& a, const BodyBox& b) {
  constexpr double half_pi = 1.5707963267948966;
  for (double axis : {a.heading, a.heading + half_pi,
                      b.heading, b.heading + half_pi}) {
    const double distance = std::abs((b.x-a.x)*std::cos(axis)+(b.y-a.y)*std::sin(axis));
    const double ra = a.half_length*std::abs(std::cos(a.heading-axis)) +
                      a.half_width*std::abs(std::sin(a.heading-axis));
    const double rb = b.half_length*std::abs(std::cos(b.heading-axis)) +
                      b.half_width*std::abs(std::sin(b.heading-axis));
    if (distance > ra + rb + 1e-9) return false;
  }
  return true;
}
}  // namespace apollo::simulation::worldsim
