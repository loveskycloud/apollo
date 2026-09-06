/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/dag_controller.h"
#include "simulation/simulator/ego_car.h"
#include "simulation/simulator/scenario_util.h"

#include "gtest/gtest.h"

#include "simulation/simulator/module_catalog.h"

namespace apollo {
namespace simulation {
namespace {

TEST(ScenarioUtilTest, BuildAndEnable) {
  auto scenario = ScenarioUtil::BuildFromRuntimeModules(
      {"PREDICTION", "PLANNING", "CONTROL"}, "s1");
  EXPECT_EQ(scenario.scenario_id(), "s1");
  EXPECT_TRUE(ScenarioUtil::IsEnabled(scenario, simulator::CONTROL));
  EXPECT_FALSE(ScenarioUtil::IsEnabled(scenario, simulator::ROUTING));
}

TEST(DagControllerTest, StartFailsWithoutEnabledModules) {
  DagController dag;
  simulator::Scenario empty;
  EXPECT_FALSE(dag.Start(empty));
  EXPECT_TRUE(dag.module_list().empty());
}

TEST(DagControllerTest, EnableMacroSemantics) {
  simulator::Scenario scenario;
  scenario.add_enabled_modules(simulator::CONTROL);
  scenario.add_enabled_modules(simulator::PLANNING);
  const simulator::Scenario& scenario_ = scenario;
  EXPECT_TRUE(ENABLE(CONTROL));
  EXPECT_TRUE(ENABLE(PLANNING));
  EXPECT_FALSE(ENABLE(PREDICTION));
}

TEST(EgoCarTest, RejectsEmptyMapOrVehicle) {
  EgoCar ego;
  EgoCar::Options opts;
  EXPECT_FALSE(ego.Init(opts));
  opts.map_dir = "/tmp/not_exist_map_dir_xyz";
  opts.vehicle_config_path = "/tmp/not_exist_vehicle_xyz";
  EXPECT_FALSE(ego.Init(opts));
}

TEST(ModuleCatalogTest, DefaultControlDag) {
  auto spec =
      ModuleCatalog::Instance().GetDefaultSpec(simulator::CONTROL);
  EXPECT_EQ(spec.name(), "control");
  EXPECT_FALSE(spec.dag_path().empty());
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
