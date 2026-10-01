#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <limits>
#include <queue>
#include <string>
#include <unordered_map>
#include <vector>

namespace apollo::simulation::ml {
struct ParkingPose { double x=0,y=0,yaw=0; int gear=1; };
inline double WrapParking(double v) { return std::remainder(v,2*M_PI); }
using ParkingPolygon=std::vector<std::pair<double,double>>;
inline bool ParkingInside(double x,double y,const ParkingPolygon& poly) {
  bool inside=false;
  if(poly.size()<3)return false;
  for(size_t i=0,j=poly.size()-1;i<poly.size();j=i++) {
    const auto [ax,ay]=poly[i];const auto [bx,by]=poly[j];
    if((ay>y)!=(by>y) && x<(bx-ax)*(y-ay)/(by-ay)+ax)inside=!inside;
  }
  return inside;
}
struct ParkingObstacle {double x,y,yaw,length,width;};
struct ParkingGeometry {
  ParkingPolygon area;
  std::vector<ParkingObstacle> obstacles;
  double front=.62,back=.1,half=.25,margin=.008;
  bool Valid(const ParkingPose& p)const {
    const double c=std::cos(p.yaw),s=std::sin(p.yaw);
    std::array<std::pair<double,double>,4> box;
    const std::array<std::pair<double,double>,4> local{{{front+margin,half+margin},{front+margin,-half-margin},{-back-margin,-half-margin},{-back-margin,half+margin}}};
    for(size_t i=0;i<4;++i)box[i]={p.x+local[i].first*c-local[i].second*s,p.y+local[i].first*s+local[i].second*c};
    for(const auto& v:box)if(!ParkingInside(v.first,v.second,area))return false;
    auto cross=[](const auto& a,const auto& b,const auto& c){return (b.first-a.first)*(c.second-a.second)-(b.second-a.second)*(c.first-a.first);};
    for(size_t i=0;i<4;++i)for(size_t j=0;j<area.size();++j) {
      const auto &a=box[i],&b=box[(i+1)%4],&c=area[j],&d=area[(j+1)%area.size()];
      if(cross(a,b,c)*cross(a,b,d)<-1e-12 && cross(c,d,a)*cross(c,d,b)<-1e-12)return false;
    }
    const double cx=p.x+(front-back)/2*std::cos(p.yaw),cy=p.y+(front-back)/2*std::sin(p.yaw);
    for(const auto& o:obstacles) {
      bool separated=false;
      for(double axis:{p.yaw,p.yaw+M_PI/2,o.yaw,o.yaw+M_PI/2}) {
        const double gap=std::abs((cx-o.x)*std::cos(axis)+(cy-o.y)*std::sin(axis))-
          ((front+back)/2+margin)*std::abs(std::cos(p.yaw-axis))-(half+margin)*std::abs(std::sin(p.yaw-axis))-
          o.length/2*std::abs(std::cos(o.yaw-axis))-o.width/2*std::abs(std::sin(o.yaw-axis));
        separated|=gap>0;
      }
      if(!separated)return false;
    }
    return true;
  }
};
// A bounded reversible bicycle search. This is an explicit geometry planner,
// not an output of the forward-only PPO network. No scene IDs or actor scripts.
class ParkingPlanner {
 public:
  using Valid=std::function<bool(const ParkingPose&)>;
  double curvature=1.8;
  bool allow_reverse=true;
  size_t expansion_limit=180000;
  size_t expanded=0;
  std::vector<ParkingPose> Plan(const ParkingPose& start,const ParkingPose& goal,const Valid& valid) {
    expanded=0;
    auto reverse_start=goal;reverse_start.gear=-goal.gear;
    auto reverse_goal=start;reverse_goal.gear=-1;
    auto reversed=[](const std::vector<ParkingPose>& backward) {
      std::vector<ParkingPose> path;
      for(size_t i=backward.size();i-->0;) {
        auto p=backward[i];p.gear=-backward[std::min(i+1,backward.size()-1)].gear;path.push_back(p);
      }
      return path;
    };
    // Preserve easy exact-goal solutions before trying the other direction.
    // A bounded forward attempt avoids exhausting the reverse-search budget
    // for departures; all attempts use identical geometry constraints.
    if(allow_reverse) {
      auto easy=Search(reverse_start,reverse_goal,valid,true,std::min<size_t>(256,expansion_limit));
      if(!easy.empty())return reversed(easy);
      auto quick=Search(start,goal,valid,false,std::min<size_t>(1200,expansion_limit));
      if(!quick.empty())return quick;
    }
    // Search outward from the exact, tightly constrained goal first. Reversing
    // a checked bicycle path preserves geometry and flips each motion gear.
    auto backward=allow_reverse?Search(reverse_start,reverse_goal,valid,true,expansion_limit):std::vector<ParkingPose>{};
    if(!backward.empty()) {
      return reversed(backward);
    }
    return Search(start,goal,valid,false,expansion_limit);
  }
 private:
  std::vector<ParkingPose> Search(const ParkingPose& start,const ParkingPose& goal,const Valid& valid,bool fixed_first,size_t budget) {
    size_t attempts=0;
    if(!valid(start)||!valid(goal))return {};
    struct Node {ParkingPose p;double cost;int parent;};
    struct Entry {double priority;int id;bool operator<(const Entry& b)const{return priority>b.priority;}};
    std::vector<Node> nodes{{start,0,-1}};
    std::priority_queue<Entry> queue;queue.push({0,0});
    struct Key {
      long x,y,h;int gear;
      bool operator==(const Key& b)const{return x==b.x&&y==b.y&&h==b.h&&gear==b.gear;}
    };
    struct Hash {size_t operator()(const Key& k)const {
      size_t h=std::hash<long>{}(k.x);
      for(long v:{k.y,k.h,static_cast<long>(k.gear)})h^=std::hash<long>{}(v)+0x9e3779b9+(h<<6)+(h>>2);
      return h;
    }};
    std::unordered_map<Key,double,Hash> costs;
    auto key=[&](const ParkingPose& p){return Key{std::lround((p.x-goal.x)/.035),
        std::lround((p.y-goal.y)/.035),std::lround(WrapParking(p.yaw)/.05235987756),p.gear};};
    auto heuristic=[&](const ParkingPose& p){return std::hypot(p.x-goal.x,p.y-goal.y)+.25*std::abs(WrapParking(p.yaw-goal.yaw));};
    costs[key(start)]=0;
    while(!queue.empty() && attempts<budget) {
      const auto entry=queue.top();queue.pop();const auto n=nodes[entry.id];
      if(n.cost>costs[key(n.p)]+1e-8)continue;
      ++expanded;++attempts;
      if(std::hypot(n.p.x-goal.x,n.p.y-goal.y)<2.5 && (!fixed_first || n.parent>=0)) {
        auto tail=Connect(n.p,goal,valid);
        // Narrow bays require heading alignment before entering. Search a
        // curved approach to a straight final segment, without widening the
        // bay or moving the target. The whole connection is collision checked.
        for(double lead:{.3,.6,.9,1.2,1.5}) {
          if(!tail.empty())break;
          auto entry=goal;entry.x-=lead*goal.gear*std::cos(goal.yaw);entry.y-=lead*goal.gear*std::sin(goal.yaw);
          if(!valid(entry))continue;
          auto finish=Connect(entry,goal,valid);if(finish.empty())continue;
          auto approach=Connect(n.p,entry,valid);if(approach.empty())continue;
          tail=std::move(approach);tail.insert(tail.end(),finish.begin()+1,finish.end());
        }
        if(!tail.empty()) {
          std::vector<ParkingPose> anchors;
          for(int i=entry.id;i>=0;i=nodes[i].parent)anchors.push_back(nodes[i].p);
          std::reverse(anchors.begin(),anchors.end());
          std::vector<ParkingPose> result{anchors.front()};
          for(size_t i=1;i<anchors.size();++i) {
            auto a=anchors[i-1];const auto b=anchors[i];
            const double turn=WrapParking(b.yaw-a.yaw),k=turn/(.07*b.gear);
            for(int j=1;j<=7;++j)result.push_back(Advance(a,.01*j*b.gear,k,b.gear));
          }
          result.insert(result.end(),tail.begin()+1,tail.end());return result;
        }
      }
      for(int gear:{1,-1})for(double fraction:{-1.,-.5,0.,.5,1.}) {
        if(!allow_reverse && gear<0)continue;
        if(fixed_first && n.parent<0 && gear!=start.gear)continue;
        const double k=fraction*curvature;
        ParkingPose p;bool safe=true;
        for(int j=1;j<=7;++j) {p=Advance(n.p,.01*j*gear,k,gear);if(!valid(p)){safe=false;break;}}
        if(!safe)continue;
        const double cost=n.cost+.07*(gear<0?1.08:1)+.01*std::abs(fraction)+(gear!=n.p.gear?.3:0);
        auto identity=key(p);auto found=costs.find(identity);
        if(found!=costs.end() && found->second<=cost)continue;
        costs[identity]=cost;nodes.push_back({p,cost,entry.id});
        queue.push({cost+1.3*heuristic(p),static_cast<int>(nodes.size()-1)});
      }
    }
    return {};
  }
 private:
  ParkingPose Advance(ParkingPose a,double ds,double k,int gear)const {
    if(std::abs(k)<1e-9){a.x+=ds*std::cos(a.yaw);a.y+=ds*std::sin(a.yaw);}
    else {a.x+=(std::sin(a.yaw+k*ds)-std::sin(a.yaw))/k;a.y-=(std::cos(a.yaw+k*ds)-std::cos(a.yaw))/k;}
    a.yaw=WrapParking(a.yaw+k*ds);a.gear=gear;return a;
  }
  std::vector<ParkingPose> Connect(const ParkingPose& a,const ParkingPose& b,const Valid& valid)const {
    const double d=std::hypot(a.x-b.x,a.y-b.y);
    if(d<1e-6 && std::abs(WrapParking(a.yaw-b.yaw))<1e-6)return {a,b};
    if(d<.005)return {};
    for(double scale:{.8,1.,1.3,1.7}) {
      const double vx=d*scale*std::cos(a.yaw)*b.gear,vy=d*scale*std::sin(a.yaw)*b.gear;
      const double wx=d*scale*std::cos(b.yaw)*b.gear,wy=d*scale*std::sin(b.yaw)*b.gear;
      std::vector<ParkingPose> path{a};bool safe=true;
      const int count=std::max(20,static_cast<int>(std::ceil(d*scale/.005)));
      for(int i=1;i<=count;++i) {
        const double t=double(i)/count,t2=t*t,t3=t2*t;
        auto value=[&](double p,double q,double v,double w){return (2*t3-3*t2+1)*p+(-2*t3+3*t2)*q+(t3-2*t2+t)*v+(t3-t2)*w;};
        auto first=[&](double p,double q,double v,double w){return (6*t2-6*t)*p+(-6*t2+6*t)*q+(3*t2-4*t+1)*v+(3*t2-2*t)*w;};
        auto second=[&](double p,double q,double v,double w){return (12*t-6)*p+(-12*t+6)*q+(6*t-4)*v+(6*t-2)*w;};
        const double dx=first(a.x,b.x,vx,wx),dy=first(a.y,b.y,vy,wy),norm=std::hypot(dx,dy);
        if(norm<1e-5 || std::abs(dx*second(a.y,b.y,vy,wy)-dy*second(a.x,b.x,vx,wx))/std::pow(norm,3)>curvature){safe=false;break;}
        ParkingPose p{value(a.x,b.x,vx,wx),value(a.y,b.y,vy,wy),WrapParking(std::atan2(dy,dx)+(b.gear<0?M_PI:0)),b.gear};
        const auto& previous=path.back();const double ds=std::hypot(p.x-previous.x,p.y-previous.y);
        // A cubic can hide a zero-derivative cusp between sampled knots.
        // Reject its 180-degree heading flip even if analytic curvature at
        // either sampled endpoint is zero (collinear reversal).
        if(std::abs(WrapParking(p.yaw-previous.yaw))>curvature*ds+1e-4 || !valid(p)){safe=false;break;}
        path.push_back(p);
      }
      if(safe)return path;
    }
    return {};
  }
};
}  // namespace apollo::simulation::ml
