/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "cyber/timer/sim_timer_registry.h"

#include <memory>

#include "gtest/gtest.h"

#include "cyber/init.h"

namespace apollo {
namespace cyber {

TEST(SimTimerRegistryTest, register_and_get) {
  auto* registry = SimTimerRegistry::Instance();
  registry->Clear();
  int count = 0;
  registry->Register("control", 10, [&count]() { ++count; });
  registry->Register("planning", 100, []() {});
  auto entries = registry->GetAll();
  ASSERT_EQ(entries.size(), 2u);
  EXPECT_EQ(entries[0].name, "control");
  EXPECT_EQ(entries[0].interval_ms, 10u);
  entries[0].process();
  EXPECT_EQ(count, 1);
  registry->Register("control", 20, [&count]() { count += 10; });
  entries = registry->GetAll();
  ASSERT_EQ(entries.size(), 2u);
  entries[0].process();
  EXPECT_EQ(count, 11);
  registry->Clear();
  EXPECT_TRUE(registry->GetAll().empty());
}

}  // namespace cyber
}  // namespace apollo

int main(int argc, char** argv) {
  testing::InitGoogleTest(&argc, argv);
  apollo::cyber::Init(argv[0]);
  return RUN_ALL_TESTS();
}
