/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_MESSAGE_CONSUMER_H_
#define SIMULATION_SIMULATOR_MESSAGE_CONSUMER_H_

#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "cyber/node/node.h"

namespace apollo {
namespace simulation {

// Publishes bag payloads into MODE_SIMULATION Intra channels.
//
// Must NOT CreateWriter<RawMessage>: BlockerManager keys by channel and
// stores a typed Blocker<T>; a RawMessage writer would occupy the channel and
// make Prediction/Planning IntraReader<T> Init() fail (dynamic_pointer_cast).
class MessageConsumer {
 public:
  using PublisherFn =
      std::function<bool(const std::string& channel, const std::string& payload)>;

  virtual ~MessageConsumer() = default;
  bool Init(const std::shared_ptr<cyber::Node>& node,
            const std::vector<std::string>& inject_channels,
            const std::map<std::string, std::string>& channel_types = {});
  virtual bool Publish(const std::string& channel, const std::string& payload);

  // Optional: override / extend channel → typed publisher (e.g. from record
  // message type). Defaults cover PnC inject channels.
  void SetPublisher(const std::string& channel, PublisherFn fn);

 private:
  void RegisterDefaultPublishers();

  std::shared_ptr<cyber::Node> node_;
  std::map<std::string, PublisherFn> publishers_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_MESSAGE_CONSUMER_H_
