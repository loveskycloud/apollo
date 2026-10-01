#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace apollo::simulation::ml {
struct State { double s, l, yaw, v; };
struct Obstacle { double s, l, vs, vl, length, width, yaw; };
using Observation = std::array<double, 48>;
using Action = std::array<double, 2>;

// Maximum separating-axis gap between physical oriented boxes. Nonpositive
// means physical overlap; a positive margin adds clearance without resizing.
inline double BoxSeparation(double x,double y,double heading,double half_length,double half_width,
                            double ox,double oy,double obstacle_heading,double obstacle_half_length,double obstacle_half_width) {
  double separation=-1e9;
  for(double axis:{heading,heading+M_PI/2,obstacle_heading,obstacle_heading+M_PI/2}) {
    const double d=std::abs((ox-x)*std::cos(axis)+(oy-y)*std::sin(axis));
    const double a=half_length*std::abs(std::cos(heading-axis))+half_width*std::abs(std::sin(heading-axis));
    const double b=obstacle_half_length*std::abs(std::cos(obstacle_heading-axis))+obstacle_half_width*std::abs(std::sin(obstacle_heading-axis));
    separation=std::max(separation,d-a-b);
  }
  return separation;
}

struct LateralTransition {
  double origin, length, a0, a1, a3, a4, a5;
  LateralTransition(const State& start,double target,double distance,double curvature)
      :origin(start.s),length(distance),a0(start.l) {
    if(!(distance>0)) throw std::runtime_error("Lateral transition needs positive distance");
    a1=std::tan(start.yaw)*(1-curvature*start.l)*distance;
    const double delta=target-start.l;
    a3=10*delta-6*a1;a4=-15*delta+8*a1;a5=6*delta-3*a1;
  }
  std::array<double,2> At(double s) const {
    const double u=std::clamp((s-origin)/length,0.,1.);
    return {a0+a1*u+a3*u*u*u+a4*std::pow(u,4)+a5*std::pow(u,5),
        (a1+3*a3*u*u+4*a4*u*u*u+5*a5*std::pow(u,4))/length};
  }
};

class Policy {
 public:
  void Load(const std::string& path) {
    std::ifstream in(path);
    std::string magic;
    int a, b, c, d;
    if (!(in >> magic >> a >> b >> c >> d) ||
        !((magic == "MLP_V2" && a == 16) || (magic == "MLP_V3" && a == 48)) ||
        b != 64 || c != 64 || d != 2) {
      throw std::runtime_error("Invalid PPO weights: " + path);
    }
    input_dim_ = a;
    weights_.resize(input_dim_ * 64 + 64 + 64 * 64 + 64 + 64 * 2 + 2);
    for (double& value : weights_) {
      if (!(in >> value) || !std::isfinite(value)) {
        throw std::runtime_error("Incomplete/nonfinite PPO weights");
      }
    }
    std::string extra;
    if (in >> extra) { throw std::runtime_error("Trailing PPO weights"); }
  }
  Action Infer(const Observation& obs) const {
    auto input=obs;
    for(int i:{9,10,11}) input[i]=std::abs(obs[i]);
    // Deterministic symmetry breaking for an exactly centered obstacle.
    if(input[5]>.5 && std::abs(input[4])<.01) input[4]=.01;
    auto mirrored=input;
    for(int i:{0,1,4,15}) mirrored[i]=-input[i];
    for(int i=16;i<input_dim_;i+=8)
      for(int j:{1,6,7}) mirrored[i+j]=-input[i+j];
    mirrored[6]=obs[7];mirrored[7]=obs[6];
    const auto a=Raw(input),b=Raw(mirrored);
    return {(a[0]-b[0])/2,(a[1]+b[1])/2};
  }
 private:
  Action Raw(const Observation& obs) const {
    std::vector<double> x(obs.begin(), obs.begin()+input_dim_);
    size_t offset = 0;
    for (int width : {64, 64, 2}) {
      std::vector<double> y(width);
      for (int i = 0; i < width; ++i) {
        y[i] = weights_.at(offset + width * x.size() + i);
        for (size_t j = 0; j < x.size(); ++j) {
          y[i] += weights_.at(offset + i * x.size() + j) * x[j];
        }
        if (width != 2) { y[i] = std::tanh(y[i]); }
      }
      offset += width * (x.size() + 1);
      x = std::move(y);
    }
    return {x[0], x[1]};
  }
 private:
  std::vector<double> weights_;
  int input_dim_=16;
};

inline Observation Observe(const State& p, const std::vector<Obstacle>& obstacles,
                           double goal, double left, double right,
                           double k0, double k2, double k5) {
  const Obstacle* nearest = nullptr;
  std::vector<const Obstacle*> visible;
  for (const auto& o : obstacles) {
    // A moving actor behind us can catch up when we slow for the destination.
    const double rear_clear = .1 + o.length/2*std::abs(std::cos(o.yaw))
        + o.width/2*std::abs(std::sin(o.yaw)) + .5;
    if (o.s-p.s < (o.vs>.05 ? -8. : -rear_clear)) continue;
    if (std::abs(o.l)>std::max(left,right)+o.width && o.l*o.vl>=0) continue;
    visible.push_back(&o);
    if (!nearest || std::abs(o.s-p.s)<std::abs(nearest->s-p.s)) nearest=&o;
  }
  Observation result{p.l, p.yaw, p.v,
          nearest ? std::clamp((nearest->s-p.s)/10,-1.,1.) : 1.,
          nearest ? nearest->l-p.l : 0., nearest ? 1. : 0.,
          left/2, right/2, std::clamp((goal-p.s)/30,0.,1.), k0,k2,k5,
          nearest ? nearest->length/2 : 0., nearest ? nearest->width/2 : 0.,
          nearest ? nearest->vs : 0., nearest ? nearest->vl : 0.};
  const auto risk=[&](const Obstacle* o) {
    const double ds=o->s-p.s;
    const double closing=ds>=0 ? p.v-o->vs : o->vs-p.v;
    return std::abs(ds)/std::max(.2,closing);
  };
  std::stable_sort(visible.begin(),visible.end(),[&](const auto* a,const auto* b) {
    return risk(a)<risk(b);
  });
  for(size_t i=0;i<std::min(size_t{4},visible.size());++i) {
    const auto& o=*visible[i];const size_t j=16+8*i;
    result[j]=std::clamp((o.s-p.s)/10,-1.,1.);result[j+1]=o.l-p.l;
    result[j+2]=1;result[j+3]=o.length/2;result[j+4]=o.width/2;
    result[j+5]=o.vs;result[j+6]=o.vl;result[j+7]=std::sin(o.yaw);
  }
  return result;
}

inline State Step(State p, const Action& a, double goal, double half_width,
                  double curvature, bool brake=false, double speed_limit=1.) {
  const double target_l=std::max(0.,half_width-.27)*std::tanh(a[0]);
  const double limit=std::min(1.,std::sqrt(.35/std::max(std::abs(curvature),.01)));
  const double target_v=brake ? 0 : std::min({speed_limit,.5*(1+std::tanh(a[1]))*limit,
                                           .25*std::max(0.,goal-p.s-.12)});
  const double acceleration=brake ? -1. : std::clamp(target_v-p.v,-1.,.6);
  const double next_v=std::max(0.,p.v+acceleration*.1), v=(p.v+next_v)/2;
  const double correction=std::clamp(2*(target_l-p.l)-2.5*p.yaw,-.8,.8);
  const double next_yaw=p.yaw+v*correction*.1, mid=(p.yaw+next_yaw)/2;
  p.s+=v*std::cos(mid)/std::max(.3,1-curvature*p.l)*.1;
  p.l+=v*std::sin(mid)*.1;
  p.yaw=next_yaw; p.v=next_v;
  return p;
}

// Diagnostic uses un-clipped perception geometry, unlike bounded NN features.
// 8 s published horizon + up to 1 s braking at the supported 1 m/s speed.
inline double OncomingTtc(const State& p,const std::vector<Obstacle>& obstacles,
                          double half_width) {
  double ttc=1e9;
  for(const auto& o:obstacles) {
    const double extent=o.length/2*std::abs(std::cos(o.yaw))+o.width/2*std::abs(std::sin(o.yaw));
    if(o.vs>=-.05 || o.s+extent<p.s-.1 || std::abs(o.l)>half_width+o.width) continue;
    const double gap=std::max(0.,o.s-extent-p.s-.62);
    ttc=std::min(ttc,gap/std::max(.2,p.v-o.vs));
  }
  return ttc;
}

// Keep distant actors from provoking side changes. Crossing actors are yielded
// to on the current corridor; a nudge is reserved for a stable obstruction.
inline Action Approach(const State& p, const std::vector<Obstacle>& obstacles,
                       double half_width, Action action, bool center_distant = true,
                       bool maintain_forward_speed = true) {
  double gap = 1e9;
  bool crossing = false;
  bool approaching = false;
  for (const auto& o : obstacles) {
    const double extent = o.length/2*std::abs(std::cos(o.yaw)) +
                          o.width/2*std::abs(std::sin(o.yaw));
    // A pedestrian just outside the lane may turn back. Keep observing the
    // adjacent 2 m corridor until it has actually cleared that area; otherwise
    // constant-velocity rollout releases the car between two reversals.
    const double corridor = std::abs(o.vl)>.05 ? std::max(2.,half_width+o.width/2+.3)
                                               : half_width+o.width/2+.3;
    if (o.s+extent < p.s-.1 || std::abs(o.l)>corridor) continue;
    const double distance = o.s-extent-p.s-.62;
    gap = std::min(gap, distance);
    // Distant-static centering must not erase a learned preparation for an
    // oncoming vehicle that will reach us within the policy's 6 s window.
    approaching |= o.vs<-.05 && distance>0 &&
                   distance/std::max(.2,p.v-o.vs)<6.;
    crossing |= std::abs(o.vl)>.05 && distance<1.5;
  }
  if (gap>1.5 && center_distant && !approaching) {
    action[0]=0;
    if(maintain_forward_speed) action[1]=std::max(0.,action[1]);
  } else if (crossing) {
    action[0]=0;
    const double target = std::clamp((gap-.35)*.6, 0., .4);
    action[1]=std::min(action[1],std::atanh(std::clamp(2*target-1,-.999999,.999999)));
  }
  for(const auto& o:obstacles) {
    const double extent=o.length/2*std::abs(std::cos(o.yaw))+o.width/2*std::abs(std::sin(o.yaw));
    const double gap=o.s-extent-p.s-.62;
    const double rear_clearance=p.s-.1-(o.s+extent);
    if(std::hypot(o.vs,o.vl)<.01 && rear_clearance<.20 && gap<1.5 && std::abs(o.l)<half_width+o.width/2)
      // Leave deceleration headroom below the 0.25 m/s close-pass bound, and
      // accelerate only after the rear has cleared the near-obstacle region.
      action[1]=std::min(action[1],std::atanh(-.6));  // target 0.20 m/s
  }
  for (const auto& o : obstacles) {
    if (o.length<.5 || o.vs<.05 || std::abs(o.vl)>.1 || std::abs(o.yaw)>.3) continue;
    const double distance=o.s-o.length/2-p.s-.62;
    if (distance < -.3 || distance > 2. || std::abs(p.l-o.l)>.25+o.width/2+.15) continue;
    // Match a lead vehicle while moving sideways into a safe passing corridor.
    // A desired lateral target is not evidence the actual body has cleared it.
    const double target=std::clamp(o.vs+.6*(distance-.65),0.,1.);
    action[1]=std::min(action[1],std::atanh(std::clamp(2*target-1,-.999999,.999999)));
  }
  return action;
}

// A pass needs enough road width until both vehicles have cleared it. Match
// the leader before a narrow section instead of committing to a side offset.
inline Action FollowThroughNarrowRoad(const State& p,const std::vector<Obstacle>& obs,
                                     double upcoming_width,Action action) {
  for(const auto& o:obs) {
    const double gap=o.s-o.length/2-p.s-.62;
    if(o.length<.5 || o.vs<.05 || std::abs(o.vl)>.15 || std::abs(o.yaw)>.4 ||
       gap<0 || gap>6 || upcoming_width>=.5+o.width+.24+.1) continue;
    action[0]=0;
    const double speed=std::clamp(o.vs+.6*(gap-.75),0.,1.);
    action[1]=std::min(action[1],std::atanh(std::clamp(2*speed-1,-.999999,.999999)));
  }
  return action;
}

inline Action HoldUntilPassed(const State& p, const std::vector<Obstacle>& obs,
                             double half_width, Action action) {
  const double range = std::max(0., half_width-.27);
  const double target = range*std::tanh(action[0]);
  if (range<=0 || std::abs(p.l)<.1 || p.l*target>=p.l*p.l) return action;
  const double rear = p.s+.26*std::cos(p.yaw)
      -.36*std::abs(std::cos(p.yaw))-.25*std::abs(std::sin(p.yaw));
  for (const auto& o : obs) {
    if (o.length<.5 || std::abs(o.vl)>.1 || std::abs(o.yaw)>.3 || o.s-p.s>2.
        || std::abs(o.l)>half_width+o.width/2 || p.l*(p.l-o.l)<=0) continue;
    const double front = o.s+o.length/2*std::abs(std::cos(o.yaw))
        +o.width/2*std::abs(std::sin(o.yaw));
    if (rear-front < .5+4*std::max(0.,o.vs-p.v)) {
      // Hold the current passing offset until the REAR has cleared the FRONT.
      // Recomputed from live geometry, including when the other vehicle stops.
      action[0] = std::atanh(std::clamp(p.l/range, -.999, .999));
      break;
    }
  }
  return action;
}

inline Action PrepareGoal(const State& p,const std::vector<Obstacle>& obs,
                          double goal,double half_width,Action action) {
  if(goal-p.s>=8) return action;
  action[0]=0;
  if(std::abs(p.l)>.12) for(const auto& o:obs) {
    if(o.length<.5 || o.vs<.05 || std::abs(o.vl)>.1 || o.s-p.s < -40 ||
       o.s-p.s>2 || std::abs(o.l)>half_width+o.width/2) continue;
    // Leave enough forward distance to return to the destination centerline.
    // If a passed vehicle would catch us while slowing, let it clear first.
    action[0]=std::atanh(std::clamp(p.l/std::max(.01,half_width-.27),-.999,.999));
    action[1]=-8;
    break;
  }
  return action;
}
}  // namespace apollo::simulation::ml
