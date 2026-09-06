/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/output_channel_recorder.h"

#include <utility>

#include "cyber/common/log.h"
#include "cyber/time/clock.h"
#include "modules/common_msgs/chassis_msgs/chassis.pb.h"
#include "modules/common_msgs/control_msgs/control_cmd.pb.h"
#include "modules/common_msgs/localization_msgs/localization.pb.h"
#include "modules/common_msgs/perception_msgs/perception_obstacle.pb.h"
#include "modules/common_msgs/planning_msgs/planning.pb.h"
#include "modules/common_msgs/prediction_msgs/prediction_obstacle.pb.h"

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
                                  const std::vector<std::string>& channels) {
  node_ = node;
  sink_ = sink;
  readers_.clear();
  if (!node_ || !sink_) {
    AERROR << "OutputChannelRecorder::Start: null node/sink";
    return false;
  }

  bool ok = true;
  for (const auto& channel : channels) {
    bool added = false;
    if (channel == "/apollo/perception/obstacles") {
      added = AddTypedReader<perception::PerceptionObstacles>(channel);
    } else if (channel == "/apollo/localization/pose") {
      added = AddTypedReader<localization::LocalizationEstimate>(channel);
    } else if (channel == "/apollo/canbus/chassis") {
      added = AddTypedReader<canbus::Chassis>(channel);
    } else if (channel == "/apollo/prediction") {
      added = AddTypedReader<prediction::PredictionObstacles>(channel);
    } else if (channel == "/apollo/planning") {
      added = AddTypedReader<planning::ADCTrajectory>(channel);
    } else if (channel == "/apollo/control") {
      added = AddTypedReader<control::ControlCommand>(channel);
    } else {
      AWARN << "OutputChannelRecorder: no typed binding for channel="
            << channel << ", skip";
      continue;
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
