#include "modules/simulation/ml_planning/planner.h"
#include "modules/simulation/ml_planning/reference_line.h"
#include "gtest/gtest.h"
namespace apollo::simulation::ml {
TEST(MLPlanning, CommandSpeedLimitBoundsAllFutureSteps) {
  State p{0,0,0,0};
  for(int i=0;i<80;++i) {
    p=Step(p,{0,8},100,1,0,false,.2);
    EXPECT_LE(p.v,.2);
  }
  EXPECT_GT(p.s,1.);
}
TEST(MLPlanning, StopCommandBrakesContinuouslyWithoutReversing) {
  State p{0,0,0,.5};
  double previous=p.v;
  for(int i=0;i<80;++i) {
    p=Step(p,{0,8},100,1,0,true,.2);
    EXPECT_GE(p.v,0.);
    EXPECT_LE(p.v,previous);
    EXPECT_LE(previous-p.v,.100001);
    previous=p.v;
  }
  EXPECT_EQ(p.v,0.);
  EXPECT_GT(p.s,0.);
}
TEST(MLPlanning, FollowLeaderBeforeNarrowRoadInsteadOfStartingPass) {
  State p{0,0,0,.7};std::vector<Obstacle> lead{{1.67,-.3,.22,0,.6,.32,0}};
  auto follow=FollowThroughNarrowRoad(p,lead,1.,{1,3});
  EXPECT_EQ(follow[0],0);
  EXPECT_NEAR(.5*(1+std::tanh(follow[1])),.22,1e-9);
  EXPECT_EQ(FollowThroughNarrowRoad(p,lead,1.8,{1,3})[0],1);
  EXPECT_EQ(FollowThroughNarrowRoad(p,{},1.,{1,3})[0],1);
}
TEST(MLPlanning, RoadBoundaryRemainsAtOriginalMapAfterReferenceSmoothing) {
  ReferenceLine reference;
  reference.Set({{0,0,.4,.6},{1,0,.4,.6},{1,1,.4,.6},{1,2,.4,.6}});
  EXPECT_TRUE(reference.ContainsRoad(.4,.37,.02));
  EXPECT_FALSE(reference.ContainsRoad(.4,.41,.02));
  EXPECT_TRUE(reference.ContainsRoad(.4,-.57,.02));
  EXPECT_FALSE(reference.ContainsRoad(.4,-.61,.02));
  EXPECT_TRUE(reference.ContainsRoad(.63,1.5,.02));
  EXPECT_FALSE(reference.ContainsRoad(.59,1.5,.02));
}
TEST(MLPlanning, LateralTransitionPreservesPoseHeadingAndTarget) {
  State p{3,.1,.08,.4};LateralTransition transition(p,-.2,2,.3);
  EXPECT_NEAR(transition.At(3)[0],p.l,1e-12);
  EXPECT_NEAR(std::atan2(transition.At(3)[1],1-.3*p.l),p.yaw,1e-12);
  EXPECT_NEAR(transition.At(5)[0],-.2,1e-12);
  EXPECT_NEAR(transition.At(5)[1],0,1e-12);
  EXPECT_NEAR(transition.At(6)[0],-.2,1e-12);
}
TEST(MLPlanning, DistantActorKeepsForwardCenteredMotion) {
  auto action=Approach({0,0,0,.8},{{8,.1,0,.5,.4,.4,0}},.8,{2,-3});
  EXPECT_EQ(action[0],0);
  EXPECT_GE(action[1],0);
}
TEST(MLPlanning, SafetyCandidateCanSlowAndOffsetOnClearBend) {
  auto action=Approach({0,0,0,.5},{},.5,{-.7,-2},false);
  EXPECT_EQ(action[0],-.7);
  EXPECT_EQ(action[1],-2);
}
TEST(MLPlanning, LeadVehicleSpeedLimitedUntilBodyHasCleared) {
  auto action=Approach({0,0,0,.8},{{1.5,.2,.2,0,.6,.32,0}},.8,{-2,3},false);
  EXPECT_LT(.5*(1+std::tanh(action[1])),.2);
}
TEST(MLPlanning, UnevenCircularSamplesDoNotCreateCurvatureSpikes) {
  std::vector<ReferencePoint> points;
  for(int i=0;i<=40;++i) {
    const double angle=i*M_PI/80;
    points.push_back({10000+1.1*std::sin(angle),9000000+1.1*(1-std::cos(angle)),.5,.5});
    if(i==20) {
      const double near=angle+.00005;
      points.push_back({10000+1.1*std::sin(near),9000000+1.1*(1-std::cos(near)),.5,.5});
    }
  }
  ReferenceLine reference;reference.Set(points);
  for(double s=.05;s<reference.length()-.05;s+=.02)
    EXPECT_LT(std::abs(reference.At(s).curvature),1.2);
}
TEST(MLPlanning, GoalPreparationWaitsForCatchUpThenCenters) {
  State p{10,.3,0,.2};
  auto wait=PrepareGoal(p,{{9,0,.4,0,.6,.3,0}},13,.8,{1,2});
  EXPECT_EQ(wait[1],-8);
  EXPECT_NEAR(.53*std::tanh(wait[0]),.3,1e-9);
  EXPECT_EQ(PrepareGoal(p,{{13,0,.4,0,.6,.3,0}},13,.8,{1,2})[0],0);
}
TEST(MLPlanning, CurvePreviewCatchesBendBetweenSparseSamples) {
  std::vector<ReferencePoint> points;
  for(int i=0;i<=60;i++) {
    const double x=i*.1;
    const double y=x>2&&x<3 ? .1*std::pow(std::sin((x-2)*M_PI),2) : 0;
    points.push_back({x,y,.6,.6});
  }
  ReferenceLine reference;reference.Set(points);
  EXPECT_LT(std::abs(reference.At(1).curvature),.03);
  EXPECT_LT(std::abs(reference.At(4).curvature),.03);
  EXPECT_TRUE(reference.HasCurve(1));
  EXPECT_FALSE(reference.HasCurve(4));
}
TEST(MLPlanning, CloseCrossingSlowsWithoutChangingSides) {
  for(double lateral:{-.3,.3}) {
    auto action=Approach({0,0,0,.8},{{1.8,lateral,0,.5,.4,.4,0}},.8,{2,3});
    EXPECT_EQ(action[0],0);
    EXPECT_LE(.5*(1+std::tanh(action[1])),.4);
  }
}
TEST(MLPlanning, StableNearObstructionAllowsNudge) {
  auto action=Approach({0,0,0,.3},{{1.8,.4,0,0,.4,.4,0}},.8,{-1,0});
  EXPECT_EQ(action[0],-1);
}
TEST(MLPlanning, AdjacentWanderingPedestrianDoesNotReleaseWaitEarly) {
  auto action=Approach({0,0,0,.5},{{1.8,-1.5,.2,.2,.4,.4,0}},.836,{2,3});
  EXPECT_EQ(action[0],0);
  EXPECT_LE(.5*(1+std::tanh(action[1])),.4);
}
TEST(MLPlanning, BoundedForwardMotion) {
  State p{2,0,0,0};
  for(int i=0;i<100;++i) {auto q=Step(p,{0,4},25,.8,0);EXPECT_GE(q.s,p.s);EXPECT_DOUBLE_EQ(q.l,0);EXPECT_LE(q.v-p.v,.06000001);EXPECT_LE(q.v,1);p=q;}
  EXPECT_GT(p.s,9);
}
TEST(MLPlanning, BrakingHasPhysicalStoppingDistance) {
  State p{2,0,0,1};for(int i=0;i<20;++i)p=Step(p,{0,4},25,.8,0,true);
  EXPECT_NEAR(p.s,2.5,1e-8);EXPECT_DOUBLE_EQ(p.v,0);
}
TEST(MLPlanning, StopsBeforeGoal) {
  State p{2,0,0,0};for(int i=0;i<600;++i)p=Step(p,{0,4},25,.8,0);
  EXPECT_NEAR(p.s,24.88,.01);EXPECT_LT(p.v,.01);
}
TEST(MLPlanning, ObservesRoadPreviewAndDynamicObstacle) {
  const auto obs=Observe({2,.1,0,.5},{{7,.3,.2,-.1,.4,.2,0}},25,.8,.9,.2,.3,.4);
  EXPECT_NEAR(obs[3],.5,1e-9);EXPECT_NEAR(obs[4],.2,1e-9);EXPECT_EQ(obs[5],1);
  EXPECT_EQ(obs[9],.2);EXPECT_EQ(obs[11],.4);EXPECT_EQ(obs[14],.2);
  EXPECT_EQ(Observe({9,0,0,0},{{7,.3,0,0,.3,.2,0}},25,.8,.8,0,0,0)[5],0);
}
TEST(MLPlanning, CurvedReferencePreservesLargeCoordinatesAndProjection) {
  std::vector<ReferencePoint> points;
  for(int i=0;i<=100;++i) {double a=i*M_PI/200;points.push_back({10000+3*std::sin(a),9000000+3*(1-std::cos(a)),.8,.8});}
  ReferenceLine line;line.Set(points);const auto p=line.At(line.length()/2);
  EXPECT_NEAR(p.heading,M_PI/4,.01);EXPECT_NEAR(p.curvature,1./3,.02);
  double s,l;ASSERT_TRUE(line.Project(p.x-.2*std::sin(p.heading),p.y+.2*std::cos(p.heading),&s,&l));
  EXPECT_NEAR(s,line.length()/2,.001);EXPECT_NEAR(l,.2,.001);
}
TEST(MLPlanning, LongStoppedVehicleRemainsVisibleUntilRearClears) {
  const Obstacle truck{-1, .3, 0, 0, 2, .4, 0};
  EXPECT_EQ(Observe({0, 0, 0, 1}, {truck}, 25, .8, .8, 0, 0, 0)[5], 1);
  EXPECT_EQ(Observe({.7, 0, 0, 1}, {truck}, 25, .8, .8, 0, 0, 0)[5], 0);
}
TEST(MLPlanning, HoldPassingOffsetUntilRearHasClearedFront) {
  const Obstacle truck{0, -.3, .5, 0, 1, .4, 0};
  const auto held=HoldUntilPassed({0, .3, 0, 1}, {truck}, .8, {0, 1});
  EXPECT_NEAR(.53*std::tanh(held[0]), .3, 1e-9);
  EXPECT_EQ(held[1], 1);
  EXPECT_EQ(HoldUntilPassed({1.2, .3, 0, 1}, {truck}, .8, {0, 1})[0], 0);
  EXPECT_EQ(HoldUntilPassed({0, -.3, 0, 1}, {{0, .3, .5, 0, 1, .4, 0}}, .8, {0, 1})[0], -held[0]);
  EXPECT_EQ(HoldUntilPassed({0, .3, 0, 1}, {{0, 2, .5, 0, 1, .4, 0}}, .8, {0, 1})[0], 0);
}
TEST(MLPlanning, InvalidPolicyAndReferenceFail) {
  Policy policy;EXPECT_THROW(policy.Load("/missing/actor"),std::runtime_error);
  ReferenceLine line;EXPECT_THROW(line.Set({{0,0,1,1}}),std::runtime_error);
}
}  // namespace apollo::simulation::ml
