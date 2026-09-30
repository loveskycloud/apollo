#pragma once
#include <set>
#include <tuple>
#include "modules/simulation/ml_planning/planner.h"
#include "modules/simulation/ml_planning/reference_line.h"

namespace apollo::simulation::ml {
// A bounded spatial search prepares a connected maneuver before committing
// to an offset beside a narrow obstacle. The caller supplies full body checks.
template<class Validate>
std::vector<State> SearchPassage(const ReferenceLine& ref, State start,
                                 double end_s, double max_k, Validate valid) {
  struct Node { State p; double x,y,h,k,cost; size_t parent; };
  const auto r=ref.At(start.s);
  std::vector<Node> arena{{start,r.x-start.l*std::sin(r.heading),
      r.y+start.l*std::cos(r.heading),r.heading+start.yaw,0,0,0}};
  std::vector<size_t> beam{0};
  for(int layer=0;layer<300;++layer) {
    std::vector<Node> next;
    for(size_t index:beam) {
      const auto old=arena[index];
      for(int turn=-10;turn<=10;++turn) {
        Node p=old;
        p.k=turn*std::min(max_k-.02,1.85)/10;
        const double step=.04,delta=p.k*step;
        p.h+=delta;p.x+=step*std::cos(old.h+delta/2);p.y+=step*std::sin(old.h+delta/2);
        if(!ref.Project(p.x,p.y,&p.p.s,&p.p.l,std::max(0.,old.p.s-.1),std::min(ref.length(),old.p.s+.3)))continue;
        p.p.yaw=std::remainder(p.h-ref.At(p.p.s).heading,2*M_PI);
        if(std::abs(p.p.yaw)>.7 || !valid(old.p,p.p))continue;
        p.cost+=.08*(p.p.l*p.p.l+2*p.p.yaw*p.p.yaw)+.0005*p.k*p.k+.001*std::pow(p.k-old.k,2);
        p.parent=index;next.push_back(p);
      }
    }
    std::stable_sort(next.begin(),next.end(),[](const auto& a,const auto& b){return a.cost-.2*a.p.s<b.cost-.2*b.p.s;});
    beam.clear();std::set<std::tuple<int,int,int>> seen;
    for(const auto& p:next) {
      const auto key=std::make_tuple(std::lround(p.p.s/.02),std::lround(p.p.l/.0075),std::lround(p.p.yaw/.02));
      if(!seen.insert(key).second)continue;
      arena.push_back(p);beam.push_back(arena.size()-1);
      if(p.p.s>=end_s) {
        std::vector<State> path;
        for(size_t i=arena.size()-1;i;i=arena[i].parent)path.push_back(arena[i].p);
        path.push_back(start);std::reverse(path.begin(),path.end());return path;
      }
      if(beam.size()>=1000)break;
    }
    if(beam.empty())return {};
  }
  return {};
}
}  // namespace apollo::simulation::ml
