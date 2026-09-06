/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/message_consumer.h"

#include <memory>
#include <string>
#include <vector>

#include "cyber/blocker/blocker_manager.h"
#include "cyber/common/log.h"
#include "modules/common_msgs/chassis_msgs/chassis.pb.h"
#include "modules/common_msgs/localization_msgs/localization.pb.h"
#include "modules/common_msgs/perception_msgs/perception_obstacle.pb.h"

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
}

void MessageConsumer::SetPublisher(const std::string& channel, PublisherFn fn) {
  publishers_[channel] = std::move(fn);
}

bool MessageConsumer::Init(const std::shared_ptr<cyber::Node>& node,
                           const std::vector<std::string>& inject_channels) {
  node_ = node;
  RegisterDefaultPublishers();
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
