/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/periodic_trigger_generator.h"
#include <limits>

#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

TEST(PeriodicTriggerGeneratorTest, Control10msWindow) {
  PeriodicTriggerGenerator gen;
  const uint64_t begin = 0;
  const uint64_t end = 30 * 1000000ULL;  // 30ms
  auto events = gen.Generate(
      begin, end, 10,
      "/apollo/simulation/periodic_trigger/control");
  ASSERT_EQ(events.size(), 4u);
  EXPECT_EQ(events[0].sim_time_ns, 0u);
  EXPECT_EQ(events[1].sim_time_ns, 10u * 1000000ULL);
  EXPECT_EQ(events[2].sim_time_ns, 20u * 1000000ULL);
  EXPECT_EQ(events[3].sim_time_ns, 30u * 1000000ULL);
}

TEST(PeriodicTriggerGeneratorTest, IncludesSingleTimestampAndAvoidsOverflow) {
  auto events = PeriodicTriggerGenerator::Generate(123, 123, 10, "timer");
  ASSERT_EQ(events.size(), 1u);
  const uint64_t end = std::numeric_limits<uint64_t>::max();
  events = PeriodicTriggerGenerator::Generate(end - 10000000, end, 10, "timer");
  ASSERT_EQ(events.size(), 2u);
  EXPECT_EQ(events.back().sim_time_ns, end);
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
