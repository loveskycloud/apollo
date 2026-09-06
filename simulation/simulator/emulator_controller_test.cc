/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/emulator_controller.h"

#include <vector>

#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

class FakeMessageSource : public IMessageSource {
 public:
  void Enqueue(SimEvent ev) { events_.push_back(std::move(ev)); }

  bool Open(const SourceConfig& /*cfg*/) override { return true; }
  bool HasNext() const override { return index_ < events_.size(); }
  bool Peek(SimEvent* out) const override {
    if (!out || index_ >= events_.size()) {
      return false;
    }
    *out = events_[index_];
    return true;
  }
  bool Next(SimEvent* out) override {
    if (!Peek(out)) {
      return false;
    }
    ++index_;
    return true;
  }
  uint64_t begin_ns() const override { return 0; }
  uint64_t end_ns() const override { return 1000; }
  uint64_t total_messages() const override { return events_.size(); }

 private:
  std::vector<SimEvent> events_;
  size_t index_ = 0;
};

class FakeMessageConsumer : public MessageConsumer {
 public:
  bool Publish(const std::string& channel, const std::string& payload) {
    last_channel_ = channel;
    last_payload_ = payload;
    ++publish_count_;
    return true;
  }
  int publish_count() const { return publish_count_; }
  const std::string& last_channel() const { return last_channel_; }

 private:
  int publish_count_ = 0;
  std::string last_channel_;
  std::string last_payload_;
};

TEST(EmulatorControllerTest, LoadAndPublishInOrder) {
  auto source = std::make_shared<FakeMessageSource>();
  SimEvent e1;
  e1.sim_time_ns = 100;
  e1.channel = "/apollo/perception/obstacles";
  e1.payload = "a";
  SimEvent e2;
  e2.sim_time_ns = 200;
  e2.channel = "/apollo/perception/obstacles";
  e2.payload = "b";
  source->Enqueue(e1);
  source->Enqueue(e2);

  FakeMessageConsumer consumer;
  MessageBuffer buffer;
  logsim::ChannelPolicy policy;
  policy.add_inject_channels("/apollo/perception/obstacles");

  EmulatorController controller;
  EmulatorController::Options opts;
  opts.source = source;
  opts.consumer = &consumer;
  opts.global_buffer = &buffer;
  opts.channel_policy = policy;
  ASSERT_TRUE(controller.Init(opts));
  ASSERT_TRUE(controller.LoadFromSource());

  ASSERT_TRUE(controller.PublishNext());
  EXPECT_EQ(consumer.publish_count(), 1);
  EXPECT_EQ(consumer.last_channel(), "/apollo/perception/obstacles");
  ASSERT_TRUE(controller.PublishNext());
  EXPECT_EQ(consumer.publish_count(), 2);
  EXPECT_FALSE(controller.PublishNext());
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
