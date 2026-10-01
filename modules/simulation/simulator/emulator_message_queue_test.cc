/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/emulator_message_queue.h"

#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

TEST(EmulatorMessageQueueTest, OrdersBySimTime) {
  EmulatorMessageQueue queue;
  SimEvent later;
  later.sim_time_ns = 200;
  later.channel = "b";
  SimEvent earlier;
  earlier.sim_time_ns = 100;
  earlier.channel = "a";
  queue.Push(later);
  queue.Push(earlier);
  SimEvent out;
  ASSERT_TRUE(queue.Pop(&out));
  EXPECT_EQ(out.sim_time_ns, 100u);
  ASSERT_TRUE(queue.Pop(&out));
  EXPECT_EQ(out.sim_time_ns, 200u);
}

TEST(FabricatedMessageQueueTest, DrainAllSorted) {
  FabricatedMessageQueue queue;
  SimEvent e2;
  e2.sim_time_ns = 20;
  SimEvent e1;
  e1.sim_time_ns = 10;
  queue.Push(e2);
  queue.Push(e1);
  auto drained = queue.DrainAll();
  ASSERT_EQ(drained.size(), 2u);
  EXPECT_EQ(drained[0].sim_time_ns, 10u);
  EXPECT_EQ(drained[1].sim_time_ns, 20u);
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
