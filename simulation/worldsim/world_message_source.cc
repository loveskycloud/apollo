#include "simulation/worldsim/world_message_source.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <set>

#include "cyber/cyber.h"
#include "modules/common/configs/vehicle_config_helper.h"
#include "modules/common_msgs/chassis_msgs/chassis.pb.h"
#include "modules/common_msgs/localization_msgs/localization.pb.h"
#include "modules/common_msgs/perception_msgs/perception_obstacle.pb.h"
#include "modules/common_msgs/planning_msgs/planning_command.pb.h"
#include "modules/common_msgs/routing_msgs/routing.pb.h"
#include "simulation/simulator/message_consumer.h"
#include "simulation/worldsim/core/scenario_loader.h"

namespace apollo {
namespace simulation {
namespace {
constexpr double kPi = 3.14159265358979323846;
double Angle(double a) { return std::remainder(a, 2.0 * kPi); }
template <typename T>
void Header(T* msg, uint64_t time, uint64_t sequence) {
  auto* header = msg->mutable_header();
  header->set_timestamp_sec(static_cast<double>(time) / 1e9);
  header->set_module_name("worldsim");
  header->set_sequence_num(static_cast<uint32_t>(sequence));
}
template <typename T>
bool Send(MessageConsumer* consumer, const std::string& channel, const T& msg) {
  std::string bytes;
  return msg.SerializeToString(&bytes) && consumer->Publish(channel, bytes);
}
}  // namespace

bool WorldMessageSource::Open(const SourceConfig& cfg) {
  config_ = cfg;
  if (cfg.paths.size() != 1 || !cfg.consumer || cfg.step_ms == 0 ||
      cfg.step_ms > 100 || 100 % cfg.step_ms != 0 || cfg.begin_ns == 0 ||
      (cfg.ego_model != "perfect_planning" && cfg.ego_model != "kinematic_control")) {
    AERROR << "World input requires one JSON, nonzero epoch, step dividing 100 ms, and an explicit supported ego model";
    return false;
  }
  worldsim::Scenario scenario;
  if (!worldsim::ScenarioLoader::LoadFromJsonFile(cfg.paths[0], &scenario) ||
      !scenario.has_ego() || !scenario.ego().has_position() ||
      !std::isfinite(scenario.duration()) || scenario.duration() <= 0 ||
      scenario.duration() > 86400) {
    AERROR << "Invalid world scenario: ego pose and duration (0,86400] required";
    return false;
  }
  std::set<std::string> ids;
  for (const auto& a : scenario.agents()) {
    if (a.id().empty() || !ids.insert(a.id()).second ||
        a.type() == worldsim::AGENT_TYPE_UNKNOWN || !a.has_position()) {
      AERROR << "Invalid or duplicate world agent: " << a.id();
      return false;
    }
  }
  if (!world_.Load(scenario)) { return false; }
  begin_ns_ = cfg.begin_ns;
  step_ns_ = static_cast<uint64_t>(cfg.step_ms) * 1000000;
  const uint64_t duration_ns = static_cast<uint64_t>(std::llround(scenario.duration() * 1e9));
  if (duration_ns % step_ns_ != 0 ||
      begin_ns_ > std::numeric_limits<uint64_t>::max() - duration_ns - step_ns_) {
    AERROR << "World duration must be an integer number of steps and not overflow clock";
    return false;
  }
  end_ns_ = begin_ns_ + duration_ns;
  next_ns_ = last_ns_ = begin_ns_;
  x_ = scenario.ego().position().x(); y_ = scenario.ego().position().y();
  z_ = scenario.ego().position().z(); heading_ = scenario.ego().heading();
  if (!std::isfinite(x_) || !std::isfinite(y_) || !std::isfinite(z_) || !std::isfinite(heading_)) {
    return false;
  }
  speed_ = acceleration_ = steering_ = 0;
  route_ready_ = callback_failed_ = control_ready_ = false;
  planning_.reset(); control_.reset(); readers_.clear();
  node_ = cyber::CreateNode("world_input");
  if (!node_) { return false; }
  readers_.push_back(node_->CreateReader<planning::ADCTrajectory>(
      "/apollo/planning", [this](const std::shared_ptr<planning::ADCTrajectory>& msg) { planning_ = msg; }));
  readers_.push_back(node_->CreateReader<control::ControlCommand>(
      "/apollo/control", [this](const std::shared_ptr<control::ControlCommand>& msg) { control_ = msg; }));
  readers_.push_back(node_->CreateReader<routing::RoutingResponse>(
      "/apollo/raw_routing_response", [this](const std::shared_ptr<routing::RoutingResponse>& msg) {
        if (!msg || msg->road_size() == 0) { callback_failed_ = true; return; }
        planning::PlanningCommand command;
        *command.mutable_header() = msg->header();
        command.set_command_id(1); command.set_is_motion_command(true);
        *command.mutable_lane_follow_command() = *msg;
        route_ready_ = Send(config_.consumer, "/apollo/planning/command", command);
        callback_failed_ = !route_ready_;
      }));
  for (const auto& reader : readers_) { if (!reader) { return false; } }
  world_.set_running(true);
  return true;
}

bool WorldMessageSource::Peek(SimEvent* out) const {
  if (!out || !HasNext()) { return false; }
  *out = SimEvent{};
  out->sim_time_ns = next_ns_;
  out->tie_breaker = 10;  // Inputs before algorithm timers at the same timestamp.
  out->channel = "/simulation/world/step";
  out->sequence = (next_ns_ - begin_ns_) / step_ns_;
  return true;
}

bool WorldMessageSource::Next(SimEvent* out) {
  if (!Peek(out)) { return false; }
  const uint64_t now = next_ns_;
  out->process = [this, now]() { return Step(now); };
  next_ns_ += step_ns_;
  return true;
}

bool WorldMessageSource::SendRoute(uint64_t now) {
  routing::RoutingRequest request;
  Header(&request, now, 0);
  auto* start = request.add_waypoint();
  start->mutable_pose()->set_x(x_); start->mutable_pose()->set_y(y_);
  start->set_heading(heading_);
  const auto points = world_.ego()->GetRoutingWaypoints();
  if (points.empty()) { AERROR << "World ego has no active routing waypoints"; return false; }
  for (const auto& point : points) {
    auto* waypoint = request.add_waypoint();
    waypoint->mutable_pose()->set_x(point.position().x());
    waypoint->mutable_pose()->set_y(point.position().y());
    if (point.has_heading()) { waypoint->set_heading(point.heading()); }
  }
  if (!Send(config_.consumer, "/apollo/raw_routing_request", request) || !route_ready_) {
    AERROR << "World routing did not produce a valid route synchronously; enable ROUTING and verify map/waypoints";
    return false;
  }
  return true;
}

bool WorldMessageSource::AdvanceEgo(uint64_t now) {
  const double dt = static_cast<double>(now - last_ns_) / 1e9;
  last_ns_ = now;
  if (config_.ego_model == "kinematic_control") {
    // Explicit stationary startup phase, bounded to one virtual second. Apollo
    // may publish a not-ready control status while building its first trajectory.
    const bool valid = control_ && control_->header().status().error_code() == common::OK &&
                       control_->has_acceleration() && control_->has_steering_target();
    if (!valid && !control_ready_ && now - begin_ns_ <= 1000000000) { return true; }
    if (!valid) {
      AERROR << "No valid control command after startup / controller became unhealthy";
      return false;
    }
    control_ready_ = true;
    if (!control_->has_acceleration() || !control_->has_steering_target()) {
      AERROR << "kinematic_control requires acceleration and steering_target; no throttle-to-acceleration fallback";
      return false;
    }
    if (control_->has_gear_location() && control_->gear_location() == canbus::Chassis::GEAR_REVERSE) {
      AERROR << "kinematic_control currently supports forward driving only";
      return false;
    }
    acceleration_ = control_->acceleration(); steering_ = control_->steering_target();
    const auto& vehicle = common::VehicleConfigHelper::GetConfig().vehicle_param();
    if (!std::isfinite(acceleration_) || !std::isfinite(steering_) ||
        vehicle.wheel_base() <= 0 || vehicle.steer_ratio() <= 0) { return false; }
    const double next_speed = std::max(0.0, speed_ + acceleration_ * dt);
    const double speed = (speed_ + next_speed) / 2.0;
    const double steer_angle = std::clamp(steering_, -100.0, 100.0) * .01 * vehicle.max_steer_angle() / vehicle.steer_ratio();
    const double yaw = speed / vehicle.wheel_base() * std::tan(steer_angle) * dt;
    x_ += speed * std::cos(heading_ + yaw / 2.0) * dt;
    y_ += speed * std::sin(heading_ + yaw / 2.0) * dt;
    heading_ = Angle(heading_ + yaw); speed_ = next_speed;
    return true;
  }
  if (!planning_ || planning_->trajectory_point_size() == 0) { return true; }
  if (planning_->gear() == canbus::Chassis::GEAR_REVERSE) {
    AERROR << "perfect_planning currently supports forward driving only";
    return false;
  }
  const double rel = static_cast<double>(now) / 1e9 - planning_->header().timestamp_sec();
  const auto& points = planning_->trajectory_point();
  auto upper = std::lower_bound(points.begin(), points.end(), rel,
      [](const common::TrajectoryPoint& p, double time) { return p.relative_time() < time; });
  if (upper == points.end()) {
    AERROR << "Planning trajectory expired before current world step";
    return false;
  }
  const auto& b = *upper;
  const auto& a = upper == points.begin() ? b : *(upper - 1);
  const double span = b.relative_time() - a.relative_time();
  const double f = span > 0 ? std::clamp((rel - a.relative_time()) / span, 0.0, 1.0) : 0;
  x_ = a.path_point().x() + f * (b.path_point().x() - a.path_point().x());
  y_ = a.path_point().y() + f * (b.path_point().y() - a.path_point().y());
  // Planning commonly emits a 2-D path. Preserve configured elevation unless
  // both interpolated samples explicitly carry a height.
  if (a.path_point().has_z() && b.path_point().has_z()) {
    z_ = a.path_point().z() + f * (b.path_point().z() - a.path_point().z());
  }
  heading_ = Angle(a.path_point().theta() + f * Angle(b.path_point().theta() - a.path_point().theta()));
  speed_ = a.v() + f * (b.v() - a.v()); acceleration_ = a.a() + f * (b.a() - a.a());
  return std::isfinite(x_) && std::isfinite(y_) && std::isfinite(heading_) && std::isfinite(speed_);
}

bool WorldMessageSource::Step(uint64_t now) {
  const double previous_heading = heading_;
  const double dt = static_cast<double>(now - last_ns_) / 1e9;
  if (callback_failed_ || !AdvanceEgo(now)) { return false; }
  const double yaw_rate = dt > 0 ? Angle(heading_ - previous_heading) / dt : 0;
  if (config_.ego_model == "perfect_planning" && speed_ > 1e-4) {
    const auto& vehicle = common::VehicleConfigHelper::GetConfig().vehicle_param();
    if (vehicle.wheel_base() <= 0 || vehicle.steer_ratio() <= 0 || vehicle.max_steer_angle() <= 0) {
      AERROR << "Perfect trajectory chassis feedback requires valid vehicle steering geometry";
      return false;
    }
    // Ideal bicycle-model feedback implied by the actual trajectory motion,
    // not the Control command (the latter is only an observer in this model).
    steering_ = std::clamp(std::atan(vehicle.wheel_base() * yaw_rate / speed_) *
        vehicle.steer_ratio() / vehicle.max_steer_angle() * 100.0, -100.0, 100.0);
  }
  if (now == begin_ns_ && !SendRoute(now)) { return false; }
  const uint64_t sequence = (now - begin_ns_) / step_ns_;
  world_.UpdateEgoPose(x_, y_, z_, heading_, speed_);
  world_.AdvanceTo(static_cast<double>(now - begin_ns_) / 1e9);
  canbus::Chassis chassis;
  Header(&chassis, now, sequence);
  chassis.set_engine_started(true); chassis.set_speed_mps(speed_);
  chassis.set_driving_mode(canbus::Chassis::COMPLETE_AUTO_DRIVE);
  chassis.set_gear_location(canbus::Chassis::GEAR_DRIVE);
  chassis.set_parking_brake(false);
  chassis.set_steering_percentage(steering_);
  localization::LocalizationEstimate localization;
  Header(&localization, now, sequence);
  auto* pose = localization.mutable_pose();
  pose->mutable_position()->set_x(x_); pose->mutable_position()->set_y(y_); pose->mutable_position()->set_z(z_);
  pose->set_heading(heading_);
  // Apollo vehicle axes +Y forward; heading is ENU +X counterclockwise.
  pose->mutable_orientation()->set_qx(0); pose->mutable_orientation()->set_qy(0);
  pose->mutable_orientation()->set_qz(std::sin((heading_ - kPi / 2.0) / 2.0));
  pose->mutable_orientation()->set_qw(std::cos((heading_ - kPi / 2.0) / 2.0));
  pose->mutable_linear_velocity()->set_x(speed_ * std::cos(heading_));
  pose->mutable_linear_velocity()->set_y(speed_ * std::sin(heading_));
  pose->mutable_linear_velocity()->set_z(0);
  pose->mutable_linear_acceleration()->set_x(acceleration_ * std::cos(heading_) - speed_ * yaw_rate * std::sin(heading_));
  pose->mutable_linear_acceleration()->set_y(acceleration_ * std::sin(heading_) + speed_ * yaw_rate * std::cos(heading_));
  pose->mutable_linear_acceleration()->set_z(0);
  pose->mutable_angular_velocity()->set_x(0);
  pose->mutable_angular_velocity()->set_y(0);
  pose->mutable_angular_velocity()->set_z(yaw_rate);
  *pose->mutable_angular_velocity_vrf() = pose->angular_velocity();
  pose->mutable_linear_acceleration_vrf()->set_x(-speed_ * yaw_rate);
  pose->mutable_linear_acceleration_vrf()->set_y(acceleration_);
  pose->mutable_linear_acceleration_vrf()->set_z(0);
  if (!Send(config_.consumer, "/apollo/canbus/chassis", chassis) ||
      !Send(config_.consumer, "/apollo/localization/pose", localization)) { return false; }
  if ((now - begin_ns_) % 100000000 == 0) {
    perception::PerceptionObstacles obstacles;
    Header(&obstacles, now, sequence);
    int id = 0;
    for (const auto& agent : world_.SnapshotAgents()) {
      ++id;  // Stable JSON declaration order, including temporarily disabled agents.
      if (!agent.enabled) { continue; }
      auto* out = obstacles.add_perception_obstacle();
      out->set_id(id); out->set_timestamp(static_cast<double>(now) / 1e9);
      out->mutable_position()->set_x(agent.x); out->mutable_position()->set_y(agent.y); out->mutable_position()->set_z(agent.z);
      out->set_theta(agent.heading);
      out->mutable_velocity()->set_x(agent.speed * std::cos(agent.heading));
      out->mutable_velocity()->set_y(agent.speed * std::sin(agent.heading));
      out->set_length(agent.length); out->set_width(agent.width); out->set_height(agent.height);
      out->set_type(agent.type == worldsim::AGENT_TYPE_PEDESTRIAN ? perception::PerceptionObstacle::PEDESTRIAN :
          agent.type == worldsim::AGENT_TYPE_STATIC ? perception::PerceptionObstacle::UNKNOWN_UNMOVABLE : perception::PerceptionObstacle::VEHICLE);
      for (const auto& corner : std::vector<std::pair<double, double>>{{1,1},{1,-1},{-1,-1},{-1,1}}) {
        auto* point = out->add_polygon_point();
        const double u = corner.first * agent.length / 2, v = corner.second * agent.width / 2;
        point->set_x(agent.x + u * std::cos(agent.heading) - v * std::sin(agent.heading));
        point->set_y(agent.y + u * std::sin(agent.heading) + v * std::cos(agent.heading)); point->set_z(agent.z);
      }
    }
    if (!Send(config_.consumer, "/apollo/perception/obstacles", obstacles)) { return false; }
  }
  return !callback_failed_;
}
}  // namespace simulation
}  // namespace apollo
