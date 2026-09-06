/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/computational_graph.h"

#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

TEST(ComputationalGraphTest, TopologyPriorityOrder) {
  ComputationalGraph graph;
  simulator::ModuleTriggerTable table;
  auto* p = table.add_modules();
  p->set_module_name("PREDICTION");
  auto* pl = table.add_modules();
  pl->set_module_name("PLANNING");
  auto* c = table.add_modules();
  c->set_module_name("CONTROL");
  ASSERT_TRUE(graph.LoadFromTable(table));
  EXPECT_LT(graph.GetPriority("PREDICTION"), graph.GetPriority("PLANNING"));
  EXPECT_LT(graph.GetPriority("PLANNING"), graph.GetPriority("CONTROL"));
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
