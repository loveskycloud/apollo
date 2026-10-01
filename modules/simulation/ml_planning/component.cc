#include "modules/simulation/ml_planning/planner.h"
#include "modules/simulation/ml_planning/model_config.h"
#include "modules/simulation/ml_planning/proto/ml_planning_config.pb.h"
#include "modules/simulation/ml_planning/reference_line.h"

#include <cstdlib>
#include <iomanip>
#include <memory>
#include <mutex>
#include <utility>
#include "cyber/component/component.h"
#include "cyber/time/clock.h"
#include "modules/common/configs/vehicle_config_helper.h"
#include "modules/common_msgs/localization_msgs/localization.pb.h"
#include "modules/common_msgs/perception_msgs/perception_obstacle.pb.h"
#include "modules/common_msgs/planning_msgs/planning.pb.h"
#include "modules/common_msgs/planning_msgs/planning_command.pb.h"
#include "modules/common_msgs/planning_msgs/pad_msg.pb.h"
#include "modules/common_msgs/external_command_msgs/command_status.pb.h"
// Temporarily disabled until external-command-proto is available. Restore these
// includes, the handlers below, and the BUILD/cyberfile.xml dependencies together.
// #include "modules/external_command/proto/temporary_stop_command.pb.h"
// #include "modules/external_command/proto/reference_line_offset_command.pb.h"
#include "modules/common_msgs/routing_msgs/routing.pb.h"
#include "modules/map/hdmap/hdmap_util.h"

namespace apollo::simulation::ml {
class MLPlanning final : public cyber::Component<perception::PerceptionObstacles> {
 public:
  bool Init() override {
    MLPlanningConfig config;
    if(!GetProtoConfig(&config) || !config.IsInitialized()) {
      AERROR << "Missing/invalid ML Planning config: " << ConfigFilePath(); return false;
    }
    static_clearance_=config.static_obstacle_clearance_m();
    if(!std::isfinite(static_clearance_) || static_clearance_<.05 || static_clearance_>.5) {
      AERROR << "static_obstacle_clearance_m must be finite and in [0.05, 0.5]";
      return false;
    }
    try {
      const auto weights=ResolveModelWeights(ConfigFilePath(),config.model_version());
      policy_.Load(weights);
      AINFO << "ML Planning model_version=" << config.model_version() << " weights=" << weights;
    } catch(const std::exception& e) {
      AERROR << "ML Planning model load failed: " << e.what(); return false;
    }
    map_=hdmap::HDMapUtil::BaseMapPtr();
    if(!map_) { AERROR << "ML Planning HDMap load failed; check map_dir in the global flagfile"; return false; }
    const auto& v=common::VehicleConfigHelper::GetConfig().vehicle_param();
    front_=v.front_edge_to_center(); back_=v.back_edge_to_center(); half_=v.width()/2;
    max_k_=1/v.min_turn_radius();
    if(std::abs(front_-.62)>.01 || std::abs(back_-.1)>.01 || std::abs(half_-.25)>.01) {
      AERROR << "Unified actor v2 requires the Ranger 0.72 x 0.5 m vehicle geometry"; return false;
    }
    writer_=node_->CreateWriter<planning::ADCTrajectory>("/apollo/planning");
    status_writer_=node_->CreateWriter<external_command::CommandStatus>(
        "/apollo/planning/command_status");
    offset_status_writer_=node_->CreateWriter<external_command::CommandStatus>(
        "/apollo/planning/reference_line_offset_command_status");
    pose_reader_=node_->CreateReader<localization::LocalizationEstimate>(
        "/apollo/localization/pose",[this](const auto& p){
          std::lock_guard<std::mutex> lock(mutex_); pose_=p;
        });
    command_reader_=node_->CreateReader<planning::PlanningCommand>(
        "/apollo/planning/command",[this](const auto& command){
          std::lock_guard<std::mutex> lock(mutex_); OnCommand(*command);
        });
    pad_reader_=node_->CreateReader<planning::PadMessage>(
        "/apollo/planning/pad",[this](const auto& pad){
          std::lock_guard<std::mutex> lock(mutex_); OnPad(*pad);
        });
    if(const char* trace=std::getenv("ML_PLANNING_TRACE")) {
      trace_.open(trace); trace_ << std::setprecision(15)
        << "timestamp,s,l,yaw,speed,action_l,action_v,shield,obstacles,curvature,remaining,brake,pass_hold";
      for(size_t i=0;i<Observation{}.size();++i) trace_ << ",obs" << i;
      trace_ << ",approach_l,approach_v,unsafe_reason,unsafe_s,oncoming_ttc_s\n";
    }
    return pose_reader_ && command_reader_ && pad_reader_ && writer_ &&
           status_writer_ && offset_status_writer_;
  }

  bool Proc(const std::shared_ptr<perception::PerceptionObstacles>& perception) override {
    std::lock_guard<std::mutex> lock(mutex_);
    const double now=cyber::Clock::NowInSeconds();
    if(!pose_ || !route_ready_) return Fail(now,route_error_.empty()?"Localization/PlanningCommand not ready":route_error_);
    if(std::abs(now-pose_->header().timestamp_sec())>.2 || std::abs(now-perception->header().timestamp_sec())>.2)
      return Fail(now,"Stale localization/perception input");
    const auto& pose=pose_->pose();
    double s,l;
    if(!reference_.Project(pose.position().x(),pose.position().y(),&s,&l,
                          std::max(0.,last_s_-1),std::min(reference_.length(),last_s_+4)))
      return Fail(now,"Ego cannot be projected onto routed centerline");
    last_s_=s;
    const auto ref=reference_.At(s);
    State initial{s,l,std::remainder(pose.heading()-ref.heading,2*M_PI),
                  std::hypot(pose.linear_velocity().x(),pose.linear_velocity().y())};
    std::vector<Obstacle> obstacles;
    for(const auto& o:perception->perception_obstacle()) {
      double os,ol;
      if(!(o.length()>0 && o.width()>0) || !reference_.Project(o.position().x(),o.position().y(),&os,&ol))
        return Fail(now,"Invalid obstacle geometry");
      const auto r=reference_.At(os);
      obstacles.push_back({os,ol,o.velocity().x()*std::cos(r.heading)+o.velocity().y()*std::sin(r.heading),
        -o.velocity().x()*std::sin(r.heading)+o.velocity().y()*std::cos(r.heading),o.length(),o.width(),o.theta()-r.heading});
    }
    const auto observation=Observe(initial,obstacles,goal_,ref.left,ref.right,ref.curvature,
        reference_.At(s+2).curvature,reference_.At(s+5).curvature);
    const Action actor_action=policy_.Infer(observation);
    const Action action=Approach(initial,obstacles,std::min(ref.left,ref.right),actor_action);
    bool pass_hold=false;
    const bool requested_stop=StopRequested();
    auto states=Rollout(initial,obstacles,requested_stop,nullptr,&pass_hold);
    int unsafe_reason=0;
    double unsafe_s=0;
    const bool shield=!Safe(states,*perception,&unsafe_reason,&unsafe_s);
    bool brake=requested_stop;
    if(shield && !requested_stop) {
      // Project the actor proposal onto feasible trajectories in the same action
      // space. Every candidate is regenerated from current inputs, never a
      // scenario-specific maneuver or a precomputed path.
      double best=std::numeric_limits<double>::infinity();
      const bool road_constraint=unsafe_reason==2 || unsafe_reason==4;
      for(double lateral:{-.95,-.85,-.75,-.5,-.25,0.,.25,.5,.75,.85,.95}) {
        for(double speed:{action[1],0.,-1.}) {
          const Action candidate{std::atanh(lateral),speed};
          auto proposed=Rollout(initial,obstacles,false,&candidate);
          const double cost=.1*std::pow(lateral-std::tanh(action[0]),2)
            +.2*std::pow(std::tanh(speed)-std::tanh(action[1]),2)
            +(road_constraint?.08:2.)*lateral*lateral
            +(road_constraint?.3:4.)*std::pow(lateral-last_lateral_,2)
            -(road_constraint?1.:.2)*(proposed.back().s-initial.s);
          if(cost<best && Safe(proposed,*perception)) {best=cost;states=std::move(proposed);}
        }
      }
      if(road_constraint || unsafe_reason==5 || reference_.HasCurve(initial.s)) {
        // Continuous position/heading transitions prepare for tight bends
        // and resume a nudge after yielding, when the proportional controller
        // alone cannot recover the required offset before a static obstacle.
        // They undergo the same road, body, collision and curvature checks.
        // A centered object needs >0.3 m offset for the full Ranger footprint.
        // Enumerate both sides up to 0.54 m; Safe still checks actual map
        // boundaries and the swept body at every intermediate pose.
        for(int lateral_index=-27;lateral_index<=27;++lateral_index) {
          const double target=lateral_index*.02;
          for(double distance:{.25,.5,.75,1.,1.5,2.,2.5,3.,4.}) {
            auto proposed=LatticeRollout(initial,obstacles,target,distance,action);
            const double cost=.15*target*target+.3*std::pow(target-initial.l,2)
                -(proposed.back().s-initial.s);
            if(cost<best && Safe(proposed,*perception)) {best=cost;states=std::move(proposed);}
          }
        }
      }
      if(!std::isfinite(best)) {
        // A safe wait is essential when no passing trajectory exists. Do not
        // let its low lateral cost beat a feasible nudge forever on a clear
        // road: evaluate explicit waits only after motion candidates fail.
        for(double lateral:{-.95,-.75,-.5,-.25,0.,.25,.5,.75,.95}) {
          const Action stopped{std::atanh(lateral),-8.};
          auto proposed=Rollout(initial,obstacles,false,&stopped);
          const double cost=std::pow(lateral-last_lateral_,2)+lateral*lateral;
          if(cost<best && Safe(proposed,*perception)) {best=cost;states=std::move(proposed);}
        }
      }
      if(!previous_states_.empty()) {
        // Frenet projection can switch branches beside a hairpin. A new
        // heuristic proposal must not discard a still-safe committed motion
        // and freeze the vehicle inside a crossing. Revalidate the remaining
        // trajectory against current world-space obstacles before reusing it.
        const double elapsed=(now-previous_time_)/.1;
        const size_t skip=static_cast<size_t>(std::max(0.,std::round(elapsed)));
        if(std::abs(elapsed-skip)<.01 && skip>0 && skip<previous_states_.size()) {
          const auto& expected=previous_states_[skip];
          if(std::abs(initial.s-expected.s)<.03 && std::abs(initial.l-expected.l)<.03 &&
             std::abs(std::remainder(initial.yaw-expected.yaw,2*M_PI))<.03 &&
             std::abs(initial.v-expected.v)<.05) {
            std::vector<State> continued(previous_states_.begin()+skip,previous_states_.end());
            continued.front()=initial;
            // Preserve the certified braking continuation too. Extending a
            // committed safe prefix only with a fresh actor action can discard
            // its safe stop and create an avoidable empty-plan interval.
            auto stopping=continued;
            while(stopping.size()<81) {
              const auto p=stopping.back();const auto r=reference_.At(p.s);
              const double range=std::max(.01,std::min(r.left,r.right)-.27);
              const Action stop{std::atanh(std::clamp(p.l/range,-.999,.999)),-8.};
              stopping.push_back(Step(p,stop,goal_,std::min(r.left,r.right),r.curvature,true,speed_limit_));
            }
            if(!std::isfinite(best) && Safe(stopping,*perception)) {
              best=0;states=std::move(stopping);
            }
            auto future=obstacles;
            for(auto& o:future) {o.s+=o.vs*(continued.size()-1)*.1;o.l+=o.vl*(continued.size()-1)*.1;}
            const auto extension=Rollout(continued.back(),future,false,nullptr);
            const size_t missing=81-continued.size();
            continued.insert(continued.end(),extension.begin()+1,extension.begin()+1+missing);
            if((!std::isfinite(best) || continued.back().s>states.back().s+.05) && Safe(continued,*perception)) {best=0;states=std::move(continued);}
          }
        }
      }
      if(!std::isfinite(best)) {
        brake=true;
        const Action stopped{0,-8};
        states=Rollout(initial,obstacles,true,&stopped);
      }
    }
    int final_reason=0;double final_s=0;
    if(!Safe(states,*perception,&final_reason,&final_s))
      return Fail(now,"Unified PPO/braking constraint="+std::to_string(final_reason)+
          " at s="+std::to_string(final_s)+" initial="+std::to_string(initial.s));
    last_lateral_=std::clamp(states[1].l/std::max(.01,std::min(ref.left,ref.right)-.27),-1.,1.);
    planning::ADCTrajectory out;
    Header(now,&out); out.mutable_estop()->set_is_estop(false);
    double distance=0;
    for(size_t i=0;i<states.size();++i) {
      const auto& p=states[i]; const auto r=reference_.At(p.s);
      auto* point=out.add_trajectory_point(); auto* path=point->mutable_path_point();
      path->set_x(r.x-p.l*std::sin(r.heading)); path->set_y(r.y+p.l*std::cos(r.heading));
      path->set_z(pose.position().z()); path->set_theta(std::remainder(r.heading+p.yaw,2*M_PI));
      if(i) distance+=std::hypot(path->x()-out.trajectory_point(i-1).path_point().x(),path->y()-out.trajectory_point(i-1).path_point().y());
      path->set_s(distance); point->set_relative_time(i*.1); point->set_v(p.v);
    }
    for(int i=0;i<out.trajectory_point_size();++i) {
      const int a=i==out.trajectory_point_size()-1 ? i-1 : i, b=a+1;
      const auto& p=out.trajectory_point(a); const auto& q=out.trajectory_point(b);
      const double ds=q.path_point().s()-p.path_point().s();
      auto* point=out.mutable_trajectory_point(i);
      point->mutable_path_point()->set_kappa(ds>1e-6 ? std::remainder(q.path_point().theta()-p.path_point().theta(),2*M_PI)/ds : 0);
      point->set_a((q.v()-p.v())/.1);
    }
    const auto destination=reference_.At(goal_);
    const bool arrived=std::hypot(pose.position().x()-destination.x,
                                 pose.position().y()-destination.y)<=.4 && initial.v<.05;
    if(!requested_stop && arrived) finished_=true;
    if(finished_ && arrived) {
      // WorldSim (and standard Planning consumers) finish routes from the
      // trajectory decision, not the external-command status topic.
      auto* complete=out.mutable_decision()->mutable_main_decision()->mutable_mission_complete();
      complete->mutable_stop_point()->set_x(destination.x);
      complete->mutable_stop_point()->set_y(destination.y);
      complete->set_stop_heading(destination.heading);
    }
    out.set_total_path_length(distance); out.set_total_path_time(8); writer_->Write(out);
    PublishStatus(now,cleared_ ? external_command::ERROR :
        (finished_ ? external_command::FINISHED : external_command::RUNNING),
        cleared_ ? "Planning cleared; a new motion command is required" : "");
    previous_states_=states;previous_time_=now;
    if(trace_) {
      trace_<<now<<','<<s<<','<<l<<','<<initial.yaw<<','<<initial.v<<','<<actor_action[0]<<','<<actor_action[1]<<','<<shield<<','<<obstacles.size()<<','<<ref.curvature<<','<<goal_-s<<','<<brake<<','<<pass_hold;
      for(double value:observation) trace_<<','<<value;
      trace_<<','<<action[0]<<','<<action[1]<<','<<unsafe_reason<<','<<unsafe_s
            <<','<<OncomingTtc(initial,obstacles,std::max(ref.left,ref.right))<<'\n';
    }
    return true;
  }

 private:
  // All callbacks and Proc share the lock: routes cannot change midway through
  // trajectory generation in real Cyber, unlike the synchronous simulator.
  bool StopRequested() const {
    return pad_stop_ || temporary_stop_ || cleared_ || finished_ || speed_limit_==0;
  }
  void PublishStatus(double now,external_command::CommandStatusType status,
                     const std::string& message="") {
    if(!status_writer_ || !has_command_) return;
    external_command::CommandStatus response;
    response.mutable_header()->set_timestamp_sec(now);
    response.mutable_header()->set_module_name("ml_planning");
    response.set_command_id(command_.command_id());
    response.set_status(status);response.set_message(message);
    status_writer_->Write(response);
  }
  void RejectCommand(const planning::PlanningCommand& command,const std::string& reason) {
    command_=command;has_command_=true;route_ready_=false;route_error_=reason;
    last_motion_key_.clear();
    previous_states_.clear();
    AERROR << reason << " command_id=" << command.command_id();
    PublishStatus(cyber::Clock::NowInSeconds(),external_command::ERROR,reason);
  }
  void OnCommand(const planning::PlanningCommand& command) {
    // Temporarily disabled: TemporaryStopCommand requires external-command-proto.
    // if(command.has_custom_command() &&
    //    command.custom_command().Is<external_command::TemporaryStopCommand>()) {
    //   external_command::TemporaryStopCommand stop;
    //   if(!command.custom_command().UnpackTo(&stop) || !stop.IsInitialized() ||
    //      (stop.mode()!=external_command::TemporaryStopCommand::STOP &&
    //       stop.mode()!=external_command::TemporaryStopCommand::CANCEL)) {
    //     RejectCommand(command,"Invalid TemporaryStopCommand");return;
    //   }
    //   temporary_stop_=stop.mode()==external_command::TemporaryStopCommand::STOP;
    //   previous_states_.clear();
    //   AINFO << "ML Planning temporary_stop=" << temporary_stop_;
    //   return;  // Preserve the route and its command_id, as standard Planning does.
    // }
    if(command.has_custom_command() || command.has_parking_command()) {
      const std::string reason="ML Planning does not support this custom/parking command";
      RejectCommand(command,reason);
      // Temporarily disabled: ReferenceLineOffsetCommand requires the same package.
      // if(command.has_custom_command() &&
      //    command.custom_command().Is<external_command::ReferenceLineOffsetCommand>()) {
      //   external_command::CommandStatus status;
      //   status.mutable_header()->set_timestamp_sec(cyber::Clock::NowInSeconds());
      //   status.set_command_id(command.command_id());status.set_status(external_command::ERROR);
      //   status.set_message(reason);offset_status_writer_->Write(status);
      // }
      return;
    }
    if(!command.IsInitialized() || !command.is_motion_command() ||
       !command.has_lane_follow_command() ||
       (command.has_target_speed() && (!std::isfinite(command.target_speed()) || command.target_speed()<0))) {
      RejectCommand(command,"Invalid lane-follow PlanningCommand or target_speed");return;
    }
    // Ignore transport retries without resetting route progress or reviving a
    // cleared/completed task. A new command_id or changed route starts a task.
    auto identity=command;
    identity.clear_header();identity.mutable_lane_follow_command()->clear_header();
    const auto key=identity.SerializeAsString();
    if(key==last_motion_key_) return;
    last_motion_key_=key;
    command_=command;has_command_=true;cleared_=false;finished_=false;
    speed_limit_=command.has_target_speed() ? std::min(1.,command.target_speed()) : 1.;
    BuildRoute(command.lane_follow_command());
    PublishStatus(cyber::Clock::NowInSeconds(),route_ready_ ? external_command::RUNNING : external_command::ERROR,route_error_);
    AINFO << "ML Planning command_id=" << command.command_id() << " route_ready=" << route_ready_;
  }
  void OnPad(const planning::PadMessage& pad) {
    if(!pad.has_action() || pad.action()==planning::PadMessage::NONE) return;
    previous_states_.clear();
    switch(pad.action()) {
      case planning::PadMessage::STOP: pad_stop_=true;break;
      case planning::PadMessage::RESUME_CRUISE: pad_stop_=false;break;
      case planning::PadMessage::CLEAR_PLANNING:
        // Keep geometry only to finish braking. RESUME cannot revive this task.
        cleared_=true;temporary_stop_=false;pad_stop_=false;finished_=false;
        PublishStatus(cyber::Clock::NowInSeconds(),external_command::ERROR,"Planning cleared");
        break;
      case planning::PadMessage::FOLLOW: break;
      default:
        last_motion_key_.clear();
        route_ready_=false;route_error_="ML Planning does not support pad action "+std::to_string(pad.action());
        AERROR << route_error_;
        PublishStatus(cyber::Clock::NowInSeconds(),external_command::ERROR,route_error_);
        break;
    }
  }
  void BuildRoute(const routing::RoutingResponse& route) {
    route_ready_=false; route_error_.clear();
    previous_states_.clear();
    try {
      if(route.status().error_code()!=common::OK || route.road_size()==0 || route.routing_request().waypoint_size()<2)
        throw std::runtime_error("Routing response has no valid path/destination");
      std::vector<routing::LaneSegment> segments;
      for(const auto& road:route.road()) {
        if(road.passage_size()!=1 || road.passage(0).change_lane_type()!=routing::FORWARD)
          throw std::runtime_error("Unified v2 requires a forward route without routing lane changes");
        for(const auto& segment:road.passage(0).segment()) {
          if(!segments.empty() && segments.back().id()==segment.id()) segments.back().set_end_s(segment.end_s());
          else segments.push_back(segment);
        }
      }
      std::vector<ReferencePoint> points;
      for(const auto& segment:segments) {
        hdmap::Id id; id.set_id(segment.id()); const auto lane=map_->GetLaneById(id);
        if(!lane) throw std::runtime_error("Routed lane missing from HDMap: "+segment.id());
        // Full end lanes retain road space for the physical vehicle at start/goal.
        const double begin=0, end=lane->total_length();
        for(double t=begin;t<end+.099;t+=.1) {
          const double ls=std::min(t,end); const auto p=lane->GetSmoothPoint(ls);
          double left,right; lane->GetWidth(ls,&left,&right);
          if(!(left>half_ && right>half_)) throw std::runtime_error("Routed lane narrower than vehicle");
          if(!points.empty() && std::hypot(p.x()-points.back().x,p.y()-points.back().y)>1.)
            throw std::runtime_error("Disconnected routing centerline");
          points.push_back({p.x(),p.y(),left,right});
        }
      }
      reference_.Set(std::move(points));
      const auto& request=route.routing_request();
      auto endpoint=[&](const routing::LaneWaypoint& w,double* s) {
        hdmap::Id id; id.set_id(w.id()); const auto lane=map_->GetLaneById(id);
        if(!lane) throw std::runtime_error("Routing endpoint lane missing");
        if(!std::isfinite(w.s()) || w.s()<0 || w.s()>lane->total_length())
          throw std::runtime_error("Routing endpoint s is outside the lane");
        const auto p=lane->GetSmoothPoint(w.s()); double l;
        if(!reference_.Project(p.x(),p.y(),s,&l)) throw std::runtime_error("Route endpoint projection failed");
      };
      endpoint(request.waypoint(0),&last_s_); endpoint(request.waypoint(request.waypoint_size()-1),&goal_);
      if(goal_<=last_s_) throw std::runtime_error("Routing destination is behind start");
      route_ready_=true;
    } catch(const std::exception& e) {route_error_=e.what(); AERROR<<route_error_;}
  }
  Action Infer(const State& p,const std::vector<Obstacle>& obs) const {
    const auto r=reference_.At(p.s);
    return policy_.Infer(Observe(p,obs,goal_,r.left,r.right,r.curvature,
        reference_.At(p.s+2).curvature,reference_.At(p.s+5).curvature));
  }
  std::vector<State> Rollout(State p,std::vector<Obstacle> obs,bool brake,const Action* candidate=nullptr,bool* pass_hold=nullptr) const {
    std::vector<State> states{p};
    for(int i=0;i<80;++i) {
      const auto r=reference_.At(p.s);
      // Explicit safety candidates must retain their lateral/slow-speed choice
      // on a clear narrow bend. The actor's distant-obstacle centering rule is
      // not allowed to collapse the whole feasible search to one trajectory.
      // Short bends can lie entirely between the actor's sparse 0/2/5 m
      // preview features. Check the whole interval before suppressing a safety
      // candidate's anticipatory lateral movement.
      const bool straight=candidate && !reference_.HasCurve(p.s);
      const auto raw=Approach(p,obs,std::min(r.left,r.right),candidate ? *candidate : Infer(p,obs),
          candidate==nullptr || straight,candidate==nullptr);
      auto action=raw;
      if(!candidate) {
        action=PrepareGoal(p,obs,goal_,std::min(r.left,r.right),action);
        action=FollowThroughNarrowRoad(p,obs,reference_.MinimumWidth(p.s,8.),action);
      }
      const double before_hold=action[0];
      // Destination centering is subordinate to clearing the other vehicle's
      // full body, including a vehicle that has stopped near the destination.
      action=HoldUntilPassed(p,obs,std::min(r.left,r.right),action);
      if(pass_hold && action[0]!=before_hold) *pass_hold=true;
      p=Step(p,action,goal_,std::min(r.left,r.right),r.curvature,brake,speed_limit_);
      states.push_back(p);
      for(auto& o:obs) {o.s+=o.vs*.1;o.l+=o.vl*.1;}
    }
    return states;
  }
  std::vector<State> LatticeRollout(State p,std::vector<Obstacle> obs,double target,
                                    double distance,Action proposal) const {
    std::unique_ptr<LateralTransition> lateral;
    std::vector<State> states{p};
    for(int i=0;i<80;++i) {
      const auto r=reference_.At(p.s);
      const double half_width=std::min(r.left,r.right);
      const double range=std::max(.01,half_width-.27);
      const Action desired{std::atanh(std::clamp(target/range,-.999,.999)),proposal[1]};
      const bool center_distant=!reference_.HasCurve(p.s) &&
          OncomingTtc(p,obs,std::max(r.left,r.right))>9.;
      const auto action=Approach(p,obs,half_width,desired,center_distant,false);
      // Spatial candidates obey the same distant-static centering gate as
      // actor trajectories. Start the transition from the actual future pose
      // when the obstruction is close, preserving position/heading continuity.
      if(!lateral && std::abs(target)>1e-6 && action[0]==0 && center_distant) {
        p=Step(p,action,goal_,half_width,r.curvature,false,speed_limit_);
        states.push_back(p);
        for(auto& o:obs) {o.s+=o.vs*.1;o.l+=o.vl*.1;}
        continue;
      }
      if(!lateral) lateral=std::make_unique<LateralTransition>(p,target,distance,r.curvature);
      const double limit=std::min(1.,std::sqrt(.35/std::max(std::abs(r.curvature),.01)));
      const double target_v=std::min({speed_limit_,.5*(1+std::tanh(action[1]))*limit,.25*std::max(0.,goal_-p.s-.12)});
      const double next_v=std::max(0.,p.v+std::clamp(target_v-p.v,-1.,.6)*.1);
      p.s+=(p.v+next_v)/2*std::cos(p.yaw)/std::max(.3,1-r.curvature*p.l)*.1;
      p.v=next_v;
      const auto offset=lateral->At(p.s);
      p.l=offset[0];p.yaw=std::atan2(offset[1],std::max(.3,1-reference_.At(p.s).curvature*p.l));
      states.push_back(p);
      for(auto& o:obs) {o.s+=o.vs*.1;o.l+=o.vl*.1;}
    }
    return states;
  }
  bool Safe(const std::vector<State>& states,const perception::PerceptionObstacles& obs,
            int* reason=nullptr,double* unsafe_s=nullptr) const {
    auto reject=[&](int code,double s) {if(reason)*reason=code;if(unsafe_s)*unsafe_s=s;return false;};
    double previous_heading=0,previous_x=0,previous_y=0;
    // Certify a braking continuation as well as the published horizon. A
    // short safe prefix must not commit the ego into a bottleneck where an
    // oncoming actor subsequently hits its stationary footprint. This uses
    // only current perception/constant velocity, never the actor's script.
    bool moving=false;
    for(const auto& o:obs.perception_obstacle()) moving|=std::hypot(o.velocity().x(),o.velocity().y())>.05;
    auto checked=states;
    if(moving) {
      auto p=checked.back();
      for(int step=0;step<11 && p.v>0;++step) {
        const auto r=reference_.At(p.s);
        const double range=std::max(.01,std::min(r.left,r.right)-.27);
        const Action stop{std::atanh(std::clamp(p.l/range,-.999,.999)),-8.};
        p=Step(p,stop,goal_,std::min(r.left,r.right),r.curvature,true,speed_limit_);
        checked.push_back(p);
      }
      if(p.v>1e-6) return reject(1,p.s);  // Never certify a truncated stop.
    }
    const size_t count=moving ? checked.size()+300 : checked.size();
    for(size_t i=0;i<count;++i) {
      const auto& p=checked[std::min(i,checked.size()-1)]; if(!std::isfinite(p.s+p.l+p.yaw+p.v) || p.s<0 || p.s>reference_.length()) return reject(1,p.s);
      const auto r=reference_.At(p.s); const double heading=r.heading+p.yaw;
      const double x=r.x-p.l*std::sin(r.heading),y=r.y+p.l*std::cos(r.heading);
      if(i) {
        const double ds=std::hypot(x-previous_x,y-previous_y);
        if(ds>1e-5 && std::abs(std::remainder(heading-previous_heading,2*M_PI)/ds)>max_k_) return reject(2,p.s);
      }
      if(i<checked.size()) for(double u:{-back_,front_}) for(double v:{-half_,half_}) {
        if(!reference_.ContainsRoad(x+u*std::cos(heading)-v*std::sin(heading),
                                    y+u*std::sin(heading)+v*std::cos(heading),.02)) return reject(4,p.s);
      }
      for(const auto& o:obs.perception_obstacle()) {
        const bool stationary=o.type()==perception::PerceptionObstacle::UNKNOWN_UNMOVABLE &&
            std::hypot(o.velocity().x(),o.velocity().y())<.01;
        const double margin=stationary ? static_clearance_ : .12;
        // The consumer interpolates world positions and headings between
        // 100 ms trajectory knots. Check the executed footprint at 10 ms too:
        // safe endpoints alone can cut inside the clearance on a tight bend.
        const int steps=i>0 && i<checked.size() ? 10 : 1;
        for(int j=1;j<=steps;++j) {
          const double f=static_cast<double>(j)/steps;
          const double t=i>0 && i<checked.size() ? (i-1+f)*.1 : i*.1;
          const double h=steps>1 ? previous_heading+f*std::remainder(heading-previous_heading,2*M_PI) : heading;
          const double px=steps>1 ? previous_x+f*(x-previous_x) : x;
          const double py=steps>1 ? previous_y+f*(y-previous_y) : y;
          const double ex=px+(front_-back_)/2*std::cos(h),ey=py+(front_-back_)/2*std::sin(h);
          const double ox=o.position().x()+o.velocity().x()*t,oy=o.position().y()+o.velocity().y()*t;
          if(BoxSeparation(ex,ey,h,(front_+back_)/2,half_,
                           ox,oy,o.theta(),o.length()/2,o.width()/2)<=margin)
            return reject(5,p.s);
        }
      }
      previous_x=x;previous_y=y;previous_heading=heading;
    }
    return true;
  }
  void Header(double now,planning::ADCTrajectory* out) {
    out->mutable_header()->set_timestamp_sec(now);out->mutable_header()->set_module_name("ml_planning_unified_ppo");
    out->mutable_header()->set_sequence_num(sequence_++);
    if(has_command_) out->mutable_routing_header()->CopyFrom(command_.header());
    out->set_gear(canbus::Chassis::GEAR_DRIVE);out->set_is_replan(true);
  }
  bool Fail(double now,const std::string& reason) {
    AERROR<<reason; planning::ADCTrajectory out; Header(now,&out);
    out.mutable_header()->mutable_status()->set_error_code(common::PLANNING_ERROR);
    out.mutable_header()->mutable_status()->set_msg(reason);
    out.mutable_estop()->set_is_estop(true);out.mutable_estop()->set_reason(reason);writer_->Write(out);
    PublishStatus(now,external_command::ERROR,reason);
    return true;  // Invalid runtime input is reported; the component keeps running.
  }
  std::mutex mutex_;
  planning::PlanningCommand command_;
  std::string last_motion_key_;
  bool has_command_=false,pad_stop_=false,temporary_stop_=false,cleared_=false,finished_=false;
  double speed_limit_=1.;
  double static_clearance_=.05;
  Policy policy_; ReferenceLine reference_;
  const hdmap::HDMap* map_=nullptr;
  double front_=0,back_=0,half_=0,max_k_=0,last_s_=0,goal_=0,last_lateral_=0;
  bool route_ready_=false; std::string route_error_; uint32_t sequence_=0;
  std::ofstream trace_;
  std::vector<State> previous_states_;
  double previous_time_=0;
  std::shared_ptr<localization::LocalizationEstimate> pose_;
  std::shared_ptr<cyber::Reader<localization::LocalizationEstimate>> pose_reader_;
  std::shared_ptr<cyber::Reader<planning::PlanningCommand>> command_reader_;
  std::shared_ptr<cyber::Reader<planning::PadMessage>> pad_reader_;
  std::shared_ptr<cyber::Writer<external_command::CommandStatus>> status_writer_,offset_status_writer_;
  std::shared_ptr<cyber::Writer<planning::ADCTrajectory>> writer_;
};
CYBER_REGISTER_COMPONENT(MLPlanning)
}  // namespace apollo::simulation::ml
