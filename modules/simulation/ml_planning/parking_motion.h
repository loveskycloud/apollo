#pragma once
#include <set>
#include "modules/simulation/ml_planning/parking_planner.h"
#include "modules/simulation/ml_planning/planner.h"

namespace apollo::simulation::ml {
struct ParkingSample { ParkingPose pose; double speed=0; };
struct ParkingTraffic { ParkingObstacle box; double vx=0,vy=0; int id=0; };
struct ParkingStop {
  std::vector<ParkingSample> samples;
  std::vector<ParkingPose> remainder;
};

// Brake along the committed geometry, preserving reverse gear. Never replace
// a moving vehicle with a zero-speed point at its current position.
inline ParkingStop StopParking(const std::vector<ParkingPose>& path,double speed,double decel=.4) {
  ParkingStop result;
  if(path.empty() || !std::isfinite(speed) || !std::isfinite(decel) || !(decel>0) || std::abs(speed)/decel>8)return result;
  const int gear=std::abs(speed)<1e-9?path.front().gear:(speed<0?-1:1);
  const double v=std::abs(speed),distance=v*v/(2*decel);
  std::vector<double> arc{0};size_t same_gear_end=path.size()-1;
  for(size_t i=1;i<path.size();++i) {
    arc.push_back(arc.back()+std::hypot(path[i].x-path[i-1].x,path[i].y-path[i-1].y));
    if(path[i].gear!=gear && same_gear_end==path.size()-1)same_gear_end=i-1;
  }
  if(distance>arc[same_gear_end]+1e-7)return result;
  auto at=[&](double s) {
    if(s>=arc.back()-1e-10){auto p=path.back();p.gear=gear;return p;}
    const size_t i=std::min(static_cast<size_t>(std::upper_bound(arc.begin(),arc.end(),s)-arc.begin()-1),path.size()-2);
    const double u=(s-arc[i])/std::max(1e-12,arc[i+1]-arc[i]);
    return ParkingPose{path[i].x+u*(path[i+1].x-path[i].x),path[i].y+u*(path[i+1].y-path[i].y),
      WrapParking(path[i].yaw+u*WrapParking(path[i+1].yaw-path[i].yaw)),gear};
  };
  for(int i=0;i<=80;++i) {
    const double t=std::min(i*.1,v/decel);
    result.samples.push_back({at(v*t-.5*decel*t*t),gear*std::max(0.,v-decel*t)});
  }
  result.remainder.push_back(result.samples.back().pose);
  for(size_t i=1;i<path.size();++i)if(arc[i]>distance+1e-8)result.remainder.push_back(path[i]);
  return result;
}

inline std::vector<ParkingSample> TimeParkingPath(const std::vector<ParkingPose>& path) {
  if(path.empty())return {};
  std::vector<ParkingSample> timed;timed.push_back({path.front(),0});
  for(size_t begin=0;begin+1<path.size();) {
    size_t end=begin+1;const int gear=path[end].gear;
    while(end+1<path.size() && path[end+1].gear==gear)++end;
    std::vector<double> arc{0};
    for(size_t i=begin+1;i<=end;++i)arc.push_back(arc.back()+std::hypot(path[i].x-path[i-1].x,path[i].y-path[i-1].y));
    const double length=arc.back();
    if(length<1e-8){begin=end;continue;}
    const double peak=std::min(.2,std::sqrt(length*.2));
    const double ramp=peak/.2,total=length/peak+ramp;
    const int steps=std::max(2,static_cast<int>(std::ceil(total/.1)));
    const double stretch=steps*.1/total;
    // Stop before every gear change. A trajectory never interpolates across
    // different gears; each next gear starts with a stationary sample.
    auto first=path[begin];first.gear=gear;
    timed.push_back({first,0});timed.push_back({first,0});
    for(int j=1;j<=steps;++j) {
      const double t=j*.1/stretch;
      double s,v;
      if(t<ramp){s=.1*t*t;v=.2*t;}
      else if(t>total-ramp){const double left=std::max(0.,total-t);s=length-.1*left*left;v=.2*left;}
      else{s=.5*peak*ramp+peak*(t-ramp);v=peak;}
      s=std::clamp(s,0.,length);
      const size_t i=std::min(static_cast<size_t>(std::upper_bound(arc.begin(),arc.end(),s)-arc.begin()-1),arc.size()-2);
      const double u=(s-arc[i])/std::max(1e-9,arc[i+1]-arc[i]);const auto &a=path[begin+i],&b=path[begin+i+1];
      timed.push_back({{a.x+u*(b.x-a.x),a.y+u*(b.y-a.y),WrapParking(a.yaw+u*WrapParking(b.yaw-a.yaw)),gear},v/stretch*gear});
    }
    timed.back().speed=0;begin=end;
  }
  return timed;
}

// Validate the interpolated 10 ms footprint, including a stopped tail, against
// current perceived constant-velocity traffic. Re-evaluated every 100 ms.
inline bool ParkingMotionSafe(const std::vector<ParkingSample>& samples,
                              const ParkingGeometry& geometry,const std::vector<ParkingTraffic>& traffic,
                              double traffic_horizon=8.) {
  if(samples.empty())return false;
  for(size_t i=0;i<samples.size();++i)for(int j=(i?1:10);j<=10;++j) {
    const auto& a=samples[i?i-1:0].pose;const auto& b=samples[i].pose;
    const double u=j*.1,t=i?(i-1+u)*.1:0;
    const ParkingPose p{a.x+u*(b.x-a.x),a.y+u*(b.y-a.y),WrapParking(a.yaw+u*WrapParking(b.yaw-a.yaw)),b.gear};
    if(!geometry.Valid(p))return false;
    const double cx=p.x+(geometry.front-geometry.back)/2*std::cos(p.yaw);
    const double cy=p.y+(geometry.front-geometry.back)/2*std::sin(p.yaw);
    for(const auto& actor:traffic) {
      if(t>traffic_horizon+1e-9)continue;
      const auto& o=actor.box;
      if(BoxSeparation(cx,cy,p.yaw,(geometry.front+geometry.back)/2,geometry.half,
          o.x+actor.vx*t,o.y+actor.vy*t,o.yaw,o.length/2,o.width/2)<=.05)return false;
    }
  }
  return true;
}
// Does a moving OBB sweep enter the remaining maneuver corridor during the maneuver?
// Continuous SAT in time avoids missing a fast crossing between sample times.
// Keep the original actors for independent executed-motion safety checks.
inline bool ParkingTrafficRelevant(const std::vector<ParkingPose>& path,
                                  const ParkingGeometry& geometry,
                                  const std::vector<ParkingTraffic>& traffic,
                                  std::set<int>* active=nullptr) {
  if(traffic.empty()){if(active)active->clear();return false;}
  if(path.empty())return true;  // No certified corridor: do not assume clearance.
  auto intersects=[&](const ParkingPose& p,const ParkingTraffic& actor) {
    const auto& o=actor.box;
    const double cx=p.x+(geometry.front-geometry.back)/2*std::cos(p.yaw);
    const double cy=p.y+(geometry.front-geometry.back)/2*std::sin(p.yaw);
    double enter=0,leave=std::hypot(actor.vx,actor.vy)<=.05?2.:30.;
    for(double axis:{p.yaw,p.yaw+M_PI/2,o.yaw,o.yaw+M_PI/2}) {
      const double c=std::cos(axis),sn=std::sin(axis);
      const double radius=(geometry.front+geometry.back)/2*std::abs(std::cos(p.yaw-axis))+
          geometry.half*std::abs(std::sin(p.yaw-axis))+o.length/2*std::abs(std::cos(o.yaw-axis))+
          o.width/2*std::abs(std::sin(o.yaw-axis))+.10;
      const double offset=(o.x-cx)*c+(o.y-cy)*sn,velocity=actor.vx*c+actor.vy*sn;
      if(std::abs(velocity)<1e-12) {if(std::abs(offset)>radius)return false;}
      else {
        double a=(-radius-offset)/velocity,b=(radius-offset)/velocity;
        if(a>b)std::swap(a,b);
        enter=std::max(enter,a);leave=std::min(leave,b);
        if(enter>leave)return false;
      }
    }
    return enter<=leave;
  };
  std::set<int> next;
  bool any=false;
  for(const auto& actor:traffic) {
    bool threat=false;
    for(size_t i=0;i<path.size() && !threat;++i) {
      const auto& a=path[i?i-1:0];const auto& b=path[i];
      const double motion=std::hypot(b.x-a.x,b.y-a.y)+
          std::hypot(geometry.front,geometry.half)*std::abs(WrapParking(b.yaw-a.yaw));
      const int steps=std::max(1,static_cast<int>(std::ceil(motion/.02)));
      for(int j=0;j<=steps && !threat;++j) {
        const double u=static_cast<double>(j)/steps;
        threat=intersects({a.x+u*(b.x-a.x),a.y+u*(b.y-a.y),WrapParking(a.yaw+u*WrapParking(b.yaw-a.yaw)),b.gear},actor);
      }
    }
    // Once a real crossing is underway, keep tracking that target until it
    // stops or exits the maneuver area. Releasing it as soon as it passes one
    // path segment can launch us into opposing traffic during the same crossing.
    // Unrelated moving actors never enter this set and cannot prolong the wait.
    if(!threat && active && active->count(actor.id)) {
      const auto& o=actor.box;
      double distance=ParkingInside(o.x,o.y,geometry.area)?0.:std::numeric_limits<double>::infinity();
      for(size_t i=0;i<geometry.area.size();++i) {
        const auto& a=geometry.area[i];const auto& b=geometry.area[(i+1)%geometry.area.size()];
        const double dx=b.first-a.first,dy=b.second-a.second,n=dx*dx+dy*dy;
        const double u=n>0?std::clamp(((o.x-a.first)*dx+(o.y-a.second)*dy)/n,0.,1.):0;
        distance=std::min(distance,std::hypot(o.x-a.first-u*dx,o.y-a.second-u*dy));
      }
      threat=distance<=std::hypot(o.length,o.width)/2+.10;
    }
    if(threat){any=true;next.insert(actor.id);}
  }
  if(active)*active=std::move(next);
  return any;
}

}  // namespace apollo::simulation::ml
