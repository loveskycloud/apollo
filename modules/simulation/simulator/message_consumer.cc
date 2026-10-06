/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/message_consumer.h"

#include <memory>
#include <string>
#include <vector>

#include "cyber/blocker/blocker_manager.h"
#include "cyber/common/log.h"
#include "modules/common_msgs/chassis_msgs/chassis.pb.h"
#include "modules/common_msgs/external_command_msgs/command_status.pb.h"
#include "modules/common_msgs/localization_msgs/localization.pb.h"
#include "modules/common_msgs/localization_msgs/gps.pb.h"
#include "modules/common_msgs/localization_msgs/imu.pb.h"
#include "modules/common_msgs/sensor_msgs/ins.pb.h"
#include "modules/common_msgs/sensor_msgs/pointcloud.pb.h"
#include "modules/common_msgs/sensor_msgs/sensor_image.pb.h"
#include "modules/common_msgs/sensor_msgs/conti_radar.pb.h"
#include "modules/common_msgs/sensor_msgs/oculii_radar.pb.h"
#include "modules/common_msgs/transform_msgs/transform.pb.h"
#include "modules/common_msgs/perception_msgs/perception_obstacle.pb.h"
#include "modules/common_msgs/planning_msgs/planning.pb.h"
#include "modules/common_msgs/planning_msgs/pad_msg.pb.h"
#include "modules/common_msgs/planning_msgs/planning_command.pb.h"
#include "modules/common_msgs/prediction_msgs/prediction_obstacle.pb.h"
#include "modules/common_msgs/routing_msgs/routing.pb.h"

namespace apollo {
namespace simulation {
namespace {

template <typename MessageT>
bool PublishTyped(const std::string& channel, const std::string& payload) {
  auto msg = std::make_shared<MessageT>();
  if (!msg->ParseFromString(payload)) {
    AERROR << "MessageConsumer: ParseFromString failed, channel=" << channel
           << " type=" << MessageT::descriptor()->full_name()
           << " bytes=" << payload.size();
    return false;
  }
  if (!cyber::blocker::BlockerManager::Instance()->template Publish<MessageT>(
          channel, msg)) {
    AERROR << "MessageConsumer: BlockerManager::Publish failed, channel="
           << channel;
    return false;
  }
  return true;
}

}  // namespace

void MessageConsumer::RegisterDefaultPublishers() {
  // Default PnC LogSim inject set (see channel_policy in task.pb.txt).
  publishers_["/apollo/perception/obstacles"] =
      &PublishTyped<perception::PerceptionObstacles>;
  publishers_["/apollo/localization/pose"] =
      &PublishTyped<localization::LocalizationEstimate>;
  publishers_["/apollo/canbus/chassis"] = &PublishTyped<canbus::Chassis>;
  publishers_["/apollo/prediction"] = &PublishTyped<prediction::PredictionObstacles>;
  publishers_["/apollo/planning"] = &PublishTyped<planning::ADCTrajectory>;
  publishers_["/apollo/planning/pad"] = &PublishTyped<planning::PadMessage>;
  publishers_["/apollo/planning/command"] = &PublishTyped<planning::PlanningCommand>;
  publishers_["/apollo/planning/command_status"] = &PublishTyped<external_command::CommandStatus>;
  publishers_["/apollo/planning_command_history"] = &PublishTyped<planning::PlanningCommand>;
  publishers_["/apollo/raw_routing_request"] = &PublishTyped<routing::RoutingRequest>;
  publishers_["/apollo/routing_response"] = &PublishTyped<routing::RoutingResponse>;
  publishers_["/apollo/raw_routing_response"] = &PublishTyped<routing::RoutingResponse>;
  publishers_["/apollo/sensor/gnss/odometry"] = &PublishTyped<localization::Gps>;
  publishers_["/apollo/sensor/gnss/corrected_imu"] =
      &PublishTyped<localization::CorrectedImu>;
  publishers_["/apollo/sensor/gnss/ins_stat"] = &PublishTyped<drivers::gnss::InsStat>;
  publishers_["/tf_static"] = &PublishTyped<transform::TransformStampeds>;
  publishers_["/tf"] = &PublishTyped<transform::TransformStampeds>;
  for (const std::string channel : {
           "/apollo/sensor/lidar16/compensator/PointCloud2",
           "/apollo/sensor/velodyne64/compensator/PointCloud2",
           "/apollo/sensor/velodyne128/compensator/PointCloud2",
           "/apollo/sensor/rslidar/up/PointCloud2"}) {
    publishers_[channel] = &PublishTyped<drivers::PointCloud>;
  }
}

void MessageConsumer::SetPublisher(const std::string& channel, PublisherFn fn) {
  publishers_[channel] = std::move(fn);
}

bool MessageConsumer::Init(const std::shared_ptr<cyber::Node>& node,
                           const std::vector<std::string>& inject_channels,
                           const std::map<std::string, std::string>& channel_types) {
  node_ = node;
  RegisterDefaultPublishers();
  for (const auto& entry : channel_types) {
    if (entry.second == drivers::PointCloud::descriptor()->full_name()) {
      publishers_[entry.first] = &PublishTyped<drivers::PointCloud>;
    } else if (entry.second == drivers::Image::descriptor()->full_name()) {
      publishers_[entry.first] = &PublishTyped<drivers::Image>;
    } else if (entry.second == drivers::ContiRadar::descriptor()->full_name()) {
      publishers_[entry.first] = &PublishTyped<drivers::ContiRadar>;
    } else if (entry.second == drivers::OculiiPointCloud::descriptor()->full_name()) {
      publishers_[entry.first] = &PublishTyped<drivers::OculiiPointCloud>;
    }
  }
  for (const auto& channel : inject_channels) {
    if (publishers_.find(channel) == publishers_.end()) {
      AERROR << "MessageConsumer: no typed publisher for inject channel: "
             << channel
             << " (MODE_SIMULATION requires matching Intra Blocker type)";
      return false;
    }
    AINFO << "MessageConsumer: inject channel ready (typed Intra): " << channel;
  }
  return true;
}

bool MessageConsumer::Publish(const std::string& channel,
                              const std::string& payload) {
  auto it = publishers_.find(channel);
  if (it == publishers_.end()) {
    AERROR << "MessageConsumer: no typed publisher for channel: " << channel;
    return false;
  }
  return it->second(channel, payload);
}

}  // namespace simulation
}  // namespace apollo
