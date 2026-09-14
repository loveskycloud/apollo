/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/emulator_controller.h"

#include <vector>

#include "gtest/gtest.h"
#include "cyber/time/clock.h"

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
  logsim::ChannelPolicy policy;
  policy.add_inject_channels("/apollo/perception/obstacles");

  EmulatorController controller;
  EmulatorController::Options opts;
  opts.source = source;
  opts.consumer = &consumer;
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

TEST(EmulatorControllerTest, TimerCallbacksExecuteAfterSameTimeInputs) {
  auto source = std::make_shared<FakeMessageSource>();
  SimEvent input;
  input.sim_time_ns = 100;
  input.channel = "input";
  input.tie_breaker = 10;
  source->Enqueue(input);
  std::vector<std::string> order;
  MessageConsumer consumer;
  consumer.SetPublisher("input", [&](const std::string&, const std::string&) {
    order.push_back("input"); return true;
  });
  EmulatorController controller;
  EmulatorController::Options options;
  options.source = source; options.consumer = &consumer;
  ASSERT_TRUE(controller.Init(options));
  ASSERT_TRUE(controller.LoadFromSource());
  EXPECT_TRUE(order.empty());  // Opening a source never runs the world ahead.
  FabricatedMessageQueue timers;
  SimEvent timer;
  timer.sim_time_ns = 100; timer.tie_breaker = 30;
  timer.type = SimEventType::TIMER_FIRE;
  timer.channel = "timer";
  cyber::Clock::SetMode(cyber::proto::MODE_MOCK);
  timer.process = [&]() {
    EXPECT_EQ(cyber::Clock::Now().ToNanosecond(), 100u);
    order.push_back("timer"); return true;
  };
  timers.Push(timer); controller.MergeFabricated(&timers);
  ASSERT_TRUE(controller.PublishNext());
  ASSERT_TRUE(controller.PublishNext());
  EXPECT_FALSE(controller.PublishNext());
  EXPECT_TRUE(controller.error().empty());
  EXPECT_EQ(order, (std::vector<std::string>{"input", "timer"}));
}

TEST(EmulatorControllerTest, FailureIsNotEndOfInput) {
  auto source = std::make_shared<FakeMessageSource>();
  SimEvent event; event.sim_time_ns = 100; event.channel = "bad";
  source->Enqueue(event);
  MessageConsumer consumer;
  consumer.SetPublisher("bad", [](const std::string&, const std::string&) { return false; });
  EmulatorController controller; EmulatorController::Options options;
  options.source = source; options.consumer = &consumer;
  ASSERT_TRUE(controller.Init(options)); ASSERT_TRUE(controller.LoadFromSource());
  EXPECT_FALSE(controller.PublishNext()); EXPECT_FALSE(controller.error().empty());
}

TEST(EmulatorControllerTest, RecurringTimersAdvanceOnlyAfterCompletedCallback) {
  auto source = std::make_shared<FakeMessageSource>();
  FakeMessageConsumer consumer;
  EmulatorController controller; EmulatorController::Options options;
  options.source = source; options.consumer = &consumer;
  ASSERT_TRUE(controller.Init(options));
  std::vector<uint64_t> fired;
  SimEvent timer;
  timer.type = SimEventType::TIMER_FIRE; timer.sim_time_ns = 100;
  timer.interval_ns = 10; timer.repeat_end_ns = 120; timer.channel = "control";
  timer.process = [&]() { fired.push_back(cyber::Clock::Now().ToNanosecond()); return true; };
  FabricatedMessageQueue timers; timers.Push(timer); controller.MergeFabricated(&timers);
  cyber::Clock::SetMode(cyber::proto::MODE_MOCK);
  while (controller.PublishNext()) {}
  EXPECT_EQ(fired, (std::vector<uint64_t>{100, 110, 120}));
  EXPECT_TRUE(controller.error().empty());
}

TEST(EmulatorControllerTest, RejectBackwardsTime) {
  auto source = std::make_shared<FakeMessageSource>();
  SimEvent event; event.sim_time_ns = 200; event.channel = "input";
  source->Enqueue(event); event.sim_time_ns = 100; source->Enqueue(event);
  FakeMessageConsumer consumer;
  EmulatorController controller; EmulatorController::Options options;
  options.source = source; options.consumer = &consumer;
  ASSERT_TRUE(controller.Init(options)); ASSERT_TRUE(controller.LoadFromSource());
  ASSERT_TRUE(controller.PublishNext()); EXPECT_FALSE(controller.PublishNext());
  EXPECT_NE(controller.error().find("non-monotonic"), std::string::npos);
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
