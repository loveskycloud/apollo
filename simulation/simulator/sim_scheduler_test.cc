/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/sim_scheduler.h"

#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

TEST(SimSchedulerTest, OneRoundEndsWhenQueuesIdle) {
  ComputationalGraph graph;
  simulator::ModuleTriggerTable table;
  auto* spec = table.add_modules();
  spec->set_module_name("FAKE");
  spec->set_kind(simulator::DATA_TRIGGERED);
  spec->set_trigger_channel("/apollo/fake/trigger");
  ASSERT_TRUE(graph.LoadFromTable(table));

  auto scheduler = SimScheduler::Instance();
  scheduler->Init(&graph);
  auto service = std::make_shared<ModuleReplayService>("FAKE");
  service->SetSpec(table.modules(0));
  scheduler->RegisterService("FAKE", service);

  MessageBuffer buffer;
  BufferedMessage msg;
  msg.channel = "/apollo/fake/trigger";
  msg.timestamp_ns = 100;
  scheduler->OnMessageReceived(msg, 200);

  EXPECT_TRUE(scheduler->RunOneRound(200, &buffer));
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
