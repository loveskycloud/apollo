// Offline kinematic reachability witness generator. Uses the production search;
// exported paths must also pass an independent polygon/swept-body audit.
#include "modules/simulation/ml_planning/parking_planner.h"
#include <iostream>
#include <iomanip>
int main() {
  using namespace apollo::simulation::ml;
  ParkingGeometry geometry;ParkingPose start,goal;size_t count;
  std::cin>>start.x>>start.y>>start.yaw>>goal.x>>goal.y>>goal.yaw>>goal.gear>>count;
  for(size_t i=0;i<count;++i){double x,y;std::cin>>x>>y;geometry.area.emplace_back(x,y);}
  std::cin>>count;
  for(size_t i=0;i<count;++i){ParkingObstacle o;std::cin>>o.x>>o.y>>o.yaw>>o.length>>o.width;geometry.obstacles.push_back(o);}
  if(!std::cin)return 2;
  ParkingPlanner planner;auto path=planner.Plan(start,goal,[&](const auto& p){return geometry.Valid(p);});
  std::cerr<<"expanded="<<planner.expanded<<" points="<<path.size()<<"\n";
  if(path.empty())return 1;
  std::cout<<std::setprecision(17);
  for(const auto& p:path)std::cout<<p.x<<","<<p.y<<","<<p.yaw<<","<<p.gear<<"\n";
}
