/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *****************************************************************************/
#pragma once

#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#include "CivetServer.h"
#include "cyber/cyber.h"
#include "modules/common_msgs/control_msgs/control_cmd.pb.h"
#include "modules/common_msgs/localization_msgs/localization.pb.h"
#include "modules/common_msgs/perception_msgs/perception_obstacle.pb.h"
#include "modules/common_msgs/prediction_msgs/prediction_obstacle.pb.h"
#include "modules/common_msgs/planning_msgs/planning.pb.h"
#include "modules/common_msgs/planning_msgs/planning_command.pb.h"
#include "modules/common_msgs/routing_msgs/routing.pb.h"

#include "modules/dreamview/backend/common/handlers/websocket_handler.h"
#include "modules/dreamview/backend/common/map_service/map_service.h"
#include "modules/dreamview/backend/common/sim_control_manager/sim_control_manager.h"
#include "modules/simulation/worldsim/core/world.h"

namespace apollo {
namespace simulation {
namespace worldsim {

/**
 * @brief WorldSim runtime bridge: WebSocket + Apollo Cyber + World tick.
 *
 * Scenario assets come from scene_editor proto JSON (LoadScenario).
 * Virtual ego closed-loop uses dreamview SimPerfectControl.
 * Non-ego agents are advanced by World and published as gt_obstacles.
 */
class SimBridge {
 public:
  using Json = nlohmann::json;
  using Connection = struct mg_connection;

  struct ModuleDef {
    std::string name;
    std::vector<std::string> dags;
  };

  SimBridge() = default;
  ~SimBridge();

  /** Bind World owned by WorldSim orchestrator (non-owning). */
  void SetWorld(World* world) { world_ = world; }

  /** @brief 初始化 cyber、writers/readers、WebSocket server 与消息 handler。 */
  bool Init();

  /** @brief 启动仿真定时器（虚拟车推进 + 真值障碍物发布）。 */
  void Start();

  /** @brief 停止定时器与 WebSocket server。 */
  void Stop();

 private:
  // ---- WebSocket 消息 handler（前端 → bridge）----
  void RegisterMessageHandlers();
  void HandleGetVehicleList(const Json &json, Connection *conn);
  void HandleGetMapList(const Json &json, Connection *conn);
  void HandleGetVehicleParam(const Json &json, Connection *conn);
  void HandleSetVehicle(const Json &json, Connection *conn);
  void HandleSetMap(const Json &json, Connection *conn);
  void HandleGetMapElements(const Json &json, Connection *conn);
  void HandleSetModules(const Json &json, Connection *conn);
  void HandleSimControl(const Json &json, Connection *conn);
  void HandleSendRouting(const Json &json, Connection *conn);
  void HandlePreviewRouting(const Json &json, Connection *conn);
  void HandleUploadObstacles(const Json &json, Connection *conn);
  /** Load Scenario proto JSON (path or inline) into World; editor does not tick. */
  void HandleLoadScenario(const Json &json, Connection *conn);

  // ---- cyber 回调（bridge ← 模块）----
  void OnPlanning(const std::shared_ptr<planning::ADCTrajectory> &trajectory);
  void OnPlanningCommand(
      const std::shared_ptr<planning::PlanningCommand> &planning_command);
  void OnRoutingResponse(
      const std::shared_ptr<routing::RoutingResponse> &routing_response);
  void OnPrediction(
      const std::shared_ptr<prediction::PredictionObstacles> &obstacles);
  void OnControlCommand(const std::shared_ptr<control::ControlCommand> &cmd);

  // ---- 仿真循环 ----
  void RunOnce();
  void ClearPlanning();
  void PublishGtObstacles();
  /** 启动 dreamview SimPerfectControl（与 dreamview_plus HMI 一致）。 */
  void EnsureSimControlStarted();
  void StopSimControl();
  void ResetSimControlPose(double x, double y, double heading);
  void BroadcastEgoState(double x, double y, double z, double heading,
                         double speed);
  /** Push World agent poses to editor (editor does not simulate agents). */
  void BroadcastAgentsState();
  void MaybeBroadcastTrajectory();
  void BroadcastPredictionObstacles(
      const prediction::PredictionObstacles &obstacles);

  // ---- 服务实现 ----
  std::vector<std::string> ScanProfiles() const;
  std::string ReadCurrentProfile() const;
  /** @return true if em profile use succeeded (or already current). */
  bool ApplyProfile(const std::string &profile);
  /** 加载全局 vehicle_param（无 profile 时的兜底）。 */
  bool ReloadVehicleConfig();
  /**
   * 优先加载 profiles/<name>/modules/common/data/vehicle_param.pb.txt，
   * 保证「选中的车型」与面板尺寸一致；找不到再回退全局。
   */
  bool ReloadVehicleConfigForProfile(const std::string &profile);
  std::vector<std::string> ScanMapDirs() const;
  std::string ResolveMapFile(const std::string &map_name) const;
  void StartStopModules();
  /** 强制 em stop 全部 PnC 相关服务（不影响前端勾选记忆）。 */
  void StopRunningModules();
  std::mutex module_ops_mutex_;
  /** 到达终点并保持静止后结束仿真、停模块。调用方已持有 mutex_。 */
  bool MaybeFinishOnArrivalLocked(double x, double y, double speed);
  void BroadcastJson(const Json &json);
  void BroadcastRuntimeLog(const std::string &level,
                           const std::string &message);
  void BroadcastHmiStatus();
  void BroadcastRoutingPath(const planning::PlanningCommand &planning_command);
  void BroadcastRoutingPathFromResponse(
      const routing::RoutingResponse &routing_response);
  void EnsureMapService();
  /** 将起点航向对齐最近车道，避免 planning 参考线投影失败。 */
  void SnapStartPoseToLaneLocked();
  /** routing 失败：停仿真并通知前端。 */
  void FailSimulationDueToRouting(const std::string &reason);
  void EnsureRoutingModuleForPreview();
  /** Neo 包缺 lane_follow_stage 配置时，从 task default_conf 补齐 symlink。 */
  void EnsurePlanningLaneFollowConf();
  void PublishPoseAt(double x, double y, double z, double heading, int frames);
  Json ExecuteLaneFollowRouting(const Json &points, double sx, double sy,
                                double sh);
  Json VehicleParamJson() const;

  void Reply(const Json &request, Connection *conn, const Json &payload);

  std::unique_ptr<CivetServer> server_;
  std::shared_ptr<apollo::dreamview::WebSocketHandler> websocket_;
  std::shared_ptr<cyber::Node> node_;

  // writers
  std::shared_ptr<cyber::Writer<prediction::PredictionObstacles>>
      gt_obstacles_writer_;
  // readers
  std::shared_ptr<cyber::Reader<localization::LocalizationEstimate>>
      localization_reader_;
  std::shared_ptr<cyber::Reader<planning::ADCTrajectory>> planning_reader_;
  std::shared_ptr<cyber::Reader<planning::PlanningCommand>>
      planning_command_reader_;
  std::shared_ptr<cyber::Reader<routing::RoutingResponse>>
      routing_response_reader_;
  std::shared_ptr<cyber::Reader<prediction::PredictionObstacles>>
      prediction_reader_;
  std::shared_ptr<cyber::Reader<control::ControlCommand>> control_reader_;

  std::unique_ptr<dreamview::MapService> map_service_;
  double last_routing_broadcast_time_ = -1.0;

  std::unique_ptr<cyber::Timer> sim_timer_;
  std::unique_ptr<cyber::Timer> fallback_timer_;

  // ---- 运行状态（mutex 保护）----
  std::mutex mutex_;
  planning::ADCTrajectory current_trajectory_;
  prediction::PredictionObstacles latest_prediction_;
  bool received_planning_ = false;
  bool received_prediction_ = false;

  bool sim_running_ = false;
  double start_x_ = 0.0;
  double start_y_ = 0.0;
  double start_z_ = 0.0;
  double start_heading_ = 0.0;
  /** 最近一次 SendRouting 终点（Apollo ENU），用于到站自动结束。 */
  bool has_destination_ = false;
  double dest_x_ = 0.0;
  double dest_y_ = 0.0;
  int arrive_hold_frames_ = 0;

  struct GtTrajectoryPoint {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
    double heading = 0.0;
    double speed = 0.0;
    double relative_time = 0.0;
  };
  struct UploadedObstacle {
    std::string id;
    std::string type;  // vehicle|pedestrian|loader|static
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
    double heading = 0.0;
    double speed = 0.0;
    double length = 0.0;
    double width = 0.0;
    double height = 0.0;
    std::vector<GtTrajectoryPoint> trajectory;
  };
  std::vector<UploadedObstacle> uploaded_obstacles_;

  std::unordered_map<std::string, bool> desired_modules_;
  std::string service_id_;
  std::string current_vehicle_;
  std::string current_map_;
  int64_t obstacle_seq_ = 0;
  int64_t frame_counter_ = 0;
  bool empty_planning_warned_ = false;
  /** SimPerfectControl 已通过 ChangeDynamicModel 启动，避免重复 Stop/Restart。 */
  bool sim_control_model_started_ = false;

  /** Non-owning; set by WorldSim. When loaded, agents drive gt_obstacles. */
  World* world_ = nullptr;
};

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
