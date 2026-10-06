/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/output_channel_recorder.h"
#include "modules/common_msgs/planning_msgs/pad_msg.pb.h"
#include "modules/common_msgs/external_command_msgs/command_status.pb.h"

#include <utility>

#include "cyber/common/log.h"
#include "cyber/time/clock.h"
#include "modules/common_msgs/chassis_msgs/chassis.pb.h"
#include "modules/common_msgs/control_msgs/control_cmd.pb.h"
#include "modules/common_msgs/localization_msgs/localization.pb.h"
#include "modules/common_msgs/localization_msgs/gps.pb.h"
#include "modules/common_msgs/localization_msgs/imu.pb.h"
#include "modules/common_msgs/localization_msgs/localization_status.pb.h"
#include "modules/common_msgs/sensor_msgs/ins.pb.h"
#include "modules/common_msgs/sensor_msgs/pointcloud.pb.h"
#include "modules/common_msgs/sensor_msgs/sensor_image.pb.h"
#include "modules/common_msgs/sensor_msgs/conti_radar.pb.h"
#include "modules/common_msgs/sensor_msgs/oculii_radar.pb.h"
#include "modules/common_msgs/perception_msgs/traffic_light_detection.pb.h"
#include "modules/common_msgs/transform_msgs/transform.pb.h"
#include "modules/common_msgs/perception_msgs/perception_obstacle.pb.h"
#include "modules/common_msgs/planning_msgs/planning.pb.h"
#include "modules/common_msgs/planning_msgs/planning_command.pb.h"
#include "modules/common_msgs/prediction_msgs/prediction_obstacle.pb.h"
#include "modules/common_msgs/routing_msgs/routing.pb.h"

namespace apollo {
namespace simulation {

template <typename MessageT>
bool OutputChannelRecorder::AddTypedReader(const std::string& channel) {
  if (!node_ || !sink_) {
    return false;
  }
  ResultSink* sink = sink_;
  auto reader = node_->CreateReader<MessageT>(
      channel, [sink, channel](const std::shared_ptr<MessageT>& msg) {
        if (!msg || !sink) {
          return;
        }
        sink->WriteTyped(channel, *msg, cyber::Clock::Now().ToNanosecond());
      });
  if (!reader) {
    AERROR << "OutputChannelRecorder: CreateReader failed for " << channel;
    return false;
  }
  readers_.push_back(reader);
  AINFO << "OutputChannelRecorder: recording " << channel << " as "
        << MessageT::descriptor()->full_name();
  return true;
}

bool OutputChannelRecorder::Start(const std::shared_ptr<cyber::Node>& node,
                                  ResultSink* sink,
                                  const std::vector<std::string>& channels,
                                  const std::map<std::string, std::string>& channel_types) {
  node_ = node;
  sink_ = sink;
  readers_.clear();
  if (!node_ || !sink_) {
    AERROR << "OutputChannelRecorder::Start: null node/sink";
    return false;
  }

  bool ok = true;
  for (const auto& channel : channels) {
    // Original bag topics are written byte-for-byte by the source/controller,
    // never subscribed as algorithm inputs or reserialized here.
    if (channel.rfind("/bag/", 0) == 0) {
      continue;
    }
    bool added = false;
    const auto schema = channel_types.find(channel);
    const std::string type = schema == channel_types.end() ? "" : schema->second;
    if (type == drivers::Image::descriptor()->full_name()) {
      added = AddTypedReader<drivers::Image>(channel);
    } else if (type == drivers::ContiRadar::descriptor()->full_name()) {
      added = AddTypedReader<drivers::ContiRadar>(channel);
    } else if (type == drivers::OculiiPointCloud::descriptor()->full_name()) {
      added = AddTypedReader<drivers::OculiiPointCloud>(channel);
    } else if (type == drivers::PointCloud::descriptor()->full_name() ||
        channel == "/apollo/sensor/lidar16/compensator/PointCloud2" ||
        channel == "/apollo/sensor/velodyne64/compensator/PointCloud2" ||
        channel == "/apollo/sensor/velodyne128/compensator/PointCloud2" ||
        channel == "/apollo/sensor/rslidar/up/PointCloud2") {
      added = AddTypedReader<drivers::PointCloud>(channel);
    } else if (channel == "/apollo/perception/obstacles") {
      added = AddTypedReader<perception::PerceptionObstacles>(channel);
    } else if (channel == "/apollo/perception/traffic_light") {
      added = AddTypedReader<perception::TrafficLightDetection>(channel);
    } else if (channel == "/apollo/localization/pose") {
      added = AddTypedReader<localization::LocalizationEstimate>(channel);
    } else if (channel == "/apollo/canbus/chassis") {
      added = AddTypedReader<canbus::Chassis>(channel);
    } else if (channel == "/apollo/sensor/gnss/odometry") {
      added = AddTypedReader<localization::Gps>(channel);
    } else if (channel == "/apollo/sensor/gnss/corrected_imu") {
      added = AddTypedReader<localization::CorrectedImu>(channel);
    } else if (channel == "/apollo/sensor/gnss/ins_stat") {
      added = AddTypedReader<drivers::gnss::InsStat>(channel);
    } else if (channel == "/apollo/localization/msf_status") {
      added = AddTypedReader<localization::LocalizationStatus>(channel);
    } else if (channel == "/tf" || channel == "/tf_static") {
      added = AddTypedReader<transform::TransformStampeds>(channel);
    } else if (channel == "/apollo/prediction") {
      added = AddTypedReader<prediction::PredictionObstacles>(channel);
    } else if (channel == "/apollo/planning") {
      added = AddTypedReader<planning::ADCTrajectory>(channel);
    } else if (channel == "/apollo/control") {
      added = AddTypedReader<control::ControlCommand>(channel);
    } else if (channel == "/apollo/planning/command" || channel == "/apollo/planning_command_history") {
      added = AddTypedReader<planning::PlanningCommand>(channel);
    } else if (channel == "/apollo/planning/pad") {
      added = AddTypedReader<planning::PadMessage>(channel);
    } else if (channel == "/apollo/planning/command_status" || channel == "/apollo/planning/reference_line_offset_command_status") {
      added = AddTypedReader<external_command::CommandStatus>(channel);
    } else if (channel == "/apollo/raw_routing_request") {
      added = AddTypedReader<routing::RoutingRequest>(channel);
    } else if (channel == "/apollo/routing_response" || channel == "/apollo/raw_routing_response") {
      added = AddTypedReader<routing::RoutingResponse>(channel);
    } else {
      AERROR << "OutputChannelRecorder: no typed binding for channel=" << channel;
      return false;
    }
    if (!added) {
      ok = false;
    }
  }
  AINFO << "OutputChannelRecorder started, readers=" << readers_.size();
  return ok && !readers_.empty();
}

void OutputChannelRecorder::Stop() {
  for (auto& reader : readers_) {
    if (reader) {
      reader->Shutdown();
    }
  }
  readers_.clear();
  sink_ = nullptr;
  node_.reset();
}

}  // namespace simulation
}  // namespace apollo
