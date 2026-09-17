/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/dag_controller.h"
#include "modules/simulation/simulator/ego_car.h"
#include "modules/simulation/simulator/scenario_util.h"

#include "gtest/gtest.h"
#include "gflags/gflags.h"
#include <unistd.h>
#include <cstdio>
#include <fstream>
#include "modules/common/configs/config_gflags.h"
#include "modules/simulation/simulator/environment_tools.h"

#include "modules/simulation/simulator/module_catalog.h"

namespace apollo {
namespace simulation {
namespace {

TEST(EnvironmentToolsTest, WidthIsReadBeforeMapLoadAndCheckedAfterModules) {
  google::FlagSaver saved;
  char path[] = "/tmp/sim-vehicle-width-XXXXXX";
  const int fd = mkstemp(path);
  ASSERT_GE(fd, 0);
  close(fd);
  {
    std::ofstream file(path);
    file << "vehicle_param { width: 0.86 }";
  }
  double half = 0.0;
  ASSERT_TRUE(ReadHalfVehicleWidth(path, &half));
  EXPECT_DOUBLE_EQ(half, 0.43);
  ASSERT_TRUE(ApplyMapVehicleFlags("/selected/map", path, half));
  EXPECT_DOUBLE_EQ(FLAGS_half_vehicle_width, 0.43);
  EXPECT_TRUE(VerifyMapVehicleFlags("/selected/map", path, half));
  FLAGS_half_vehicle_width = 1.05;
  EXPECT_FALSE(VerifyMapVehicleFlags("/selected/map", path, half));
  {
    std::ofstream file(path);
    file << "vehicle_param {}";
  }
  EXPECT_FALSE(ReadHalfVehicleWidth(path, &half));
  std::remove(path);
}

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
