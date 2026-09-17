/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/message_buffer.h"

#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

TEST(MessageBufferTest, PickRequiredDefaultRespectsTimestamp) {
  MessageBuffer buffer;
  BufferedMessage old_msg;
  old_msg.timestamp_ns = 100;
  old_msg.channel = "/apollo/prediction";
  BufferedMessage new_msg;
  new_msg.timestamp_ns = 300;
  new_msg.channel = "/apollo/prediction";
  buffer.Push(old_msg);
  buffer.Push(new_msg);

  auto picked = buffer.PickRequiredDefault(
      "PLANNING", 200, {"/apollo/prediction"});
  ASSERT_EQ(picked.size(), 1u);
  EXPECT_EQ(picked[0].timestamp_ns, 100u);
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
