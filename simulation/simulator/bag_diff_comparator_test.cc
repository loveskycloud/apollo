/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/bag_diff_comparator.h"

#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

TEST(BagDiffComparatorTest, IdenticalRecordsPass) {
  BagDiffComparator cmp;
  OutputRecord r1;
  r1.channel = "/apollo/planning";
  r1.sim_time_ns = 100;
  r1.content = "abc";
  OutputRecord r2 = r1;
  auto result = cmp.Compare({r1}, {r2});
  EXPECT_EQ(result.result, "PASS");
  EXPECT_TRUE(result.diffs.empty());
}

TEST(BagDiffComparatorTest, DifferentContentFails) {
  BagDiffComparator cmp;
  OutputRecord left;
  left.channel = "/apollo/planning";
  left.sim_time_ns = 100;
  left.content = "abc";
  OutputRecord right = left;
  right.content = "xyz";
  auto result = cmp.Compare({left}, {right});
  EXPECT_NE(result.result, "PASS");
  EXPECT_FALSE(result.diffs.empty());
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
