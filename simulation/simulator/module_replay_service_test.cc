/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/module_replay_service.h"

#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

TEST(ModuleReplayServiceTest, NonTriggerBuffersOnly) {
  ModuleReplayService service("PLANNING");
  simulator::ModuleTriggerSpec spec;
  spec.set_module_name("PLANNING");
  spec.set_kind(simulator::DATA_TRIGGERED);
  spec.set_trigger_channel("/apollo/prediction");
  service.SetSpec(spec);

  BufferedMessage msg;
  msg.channel = "/apollo/localization/pose";
  msg.timestamp_ns = 100;
  bool ready = true;
  service.OnMessage(msg, 200, &ready);
  EXPECT_FALSE(ready);
  EXPECT_EQ(service.GetReadiness(), TaskReadiness::NOT_READY);
}

TEST(ModuleReplayServiceTest, TriggerReadyWhenClockAllows) {
  ModuleReplayService service("PLANNING");
  simulator::ModuleTriggerSpec spec;
  spec.set_module_name("PLANNING");
  spec.set_kind(simulator::DATA_TRIGGERED);
  spec.set_trigger_channel("/apollo/prediction");
  service.SetSpec(spec);

  BufferedMessage msg;
  msg.channel = "/apollo/prediction";
  msg.timestamp_ns = 100;
  bool ready = false;
  service.OnMessage(msg, 200, &ready);
  EXPECT_TRUE(ready);
  EXPECT_EQ(service.GetReadiness(), TaskReadiness::READY);
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
