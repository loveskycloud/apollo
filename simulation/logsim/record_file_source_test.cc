/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/logsim/record_file_source.h"

#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

TEST(RecordFileSourceTest, OpenFailsWithoutPaths) {
  RecordFileSource source;
  SourceConfig cfg;
  EXPECT_FALSE(source.Open(cfg));
  EXPECT_FALSE(source.HasNext());
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
