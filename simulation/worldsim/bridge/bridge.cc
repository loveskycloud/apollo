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
#include "modules/simulation/worldsim/bridge/bridge.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <sstream>
#include <vector>
#include <sys/wait.h>
#include <thread>

#include "cyber/common/file.h"
#include "cyber/time/clock.h"
#include "gflags/gflags.h"
#include "modules/common/adapters/adapter_gflags.h"
#include "modules/common/configs/config_gflags.h"
#include "modules/common/configs/vehicle_config_helper.h"
#include "modules/common/util/json_util.h"
#include "modules/common/util/message_util.h"
#include "modules/common/util/util.h"
#include "modules/common_msgs/config_msgs/vehicle_config.pb.h"
#include "modules/common_msgs/external_command_msgs/command_status.pb.h"
#include "modules/common_msgs/external_command_msgs/lane_follow_command.pb.h"
#include "modules/common_msgs/map_msgs/map.pb.h"
#include "modules/map/hdmap/hdmap_util.h"
#include "modules/dreamview/backend/common/dreamview_gflags.h"
#include "modules/dreamview/backend/common/sim_control_manager/sim_control_manager.h"
#include "modules/simulation/worldsim/core/scenario_loader.h"

DEFINE_int32(sim_bridge_ws_port, 8889, "WebSocket port of sim bridge");
DEFINE_string(sim_bridge_profiles_path, "profiles",
              "Apollo vehicle profiles root (em profile list/use)");
DEFINE_string(sim_bridge_global_vehicle_config,
              "modules/common/data/vehicle_param.pb.txt",
              "Runtime vehicle param file after em profile use overlay");
DEFINE_double(sim_bridge_default_target_speed, 4.0,
              "Default target speed (m/s) for lane follow command");
DEFINE_string(sim_bridge_maps_data_path, "modules/map/data",
              "Root directory containing map folders");
DEFINE_string(sim_bridge_service_id, "",
              "Unique service id (SB-xxxxxx). Empty = derive from MAC address");
DEFINE_bool(sim_bridge_mdns, true,
            "Announce simbridge-<id>._simbridge._tcp via avahi-publish if available");

namespace apollo {
namespace simulation {
namespace worldsim {

using apollo::common::util::FillHeader;
using apollo::common::util::JsonUtil;
using apollo::cyber::Clock;
using apollo::external_command::CommandStatus;
using apollo::external_command::CommandStatusType;
using apollo::external_command::LaneFollowCommand;
using apollo::localization::LocalizationEstimate;
using apollo::planning::ADCTrajectory;
using apollo::planning::PlanningCommand;
using apollo::routing::RoutingResponse;
using apollo::dreamview::MapService;
using apollo::dreamview::SimControlManager;
using Json = nlohmann::json;

namespace {

double HeadingToward(double x1, double y1, double x2, double y2) {
  return std::atan2(y2 - y1, x2 - x1);
}

}  // namespace

namespace {
constexpr int kSimIntervalMs = 10;         // 100Hz UI 轮询（EgoState/轨迹广播）
constexpr int kFallbackIntervalMs = 100;   // 10Hz GT perception for fake_prediction
constexpr int kEgoStateEveryN = 5;         // 100Hz / 5 = 20Hz EgoState
constexpr int kTrajectoryEveryN = 10;      // 10Hz PlanningTrajectory
constexpr int kPredictionEveryN = 10;      // 10Hz PredictionObstacles to UI

std::string ShellQuote(const std::string &s) {
  std::string out = "'";
  for (char c : s) {
    if (c == '\'') {
      out += "'\\''";
    } else {
      out += c;
    }
  }
  out += "'";
  return out;
}

/** 从首个非 lo 网卡的 MAC 派生唯一服务 ID：SB- + FNV-1a(mac) 低 24bit */
std::string DeriveServiceId() {
  std::error_code ec;
  for (const auto &entry :
       std::filesystem::directory_iterator("/sys/class/net", ec)) {
    const std::string name = entry.path().filename().string();
    if (name == "lo") {
      continue;
    }
    std::ifstream mac_file(entry.path() / "address");
    std::string mac;
    std::getline(mac_file, mac);
    if (mac.size() < 17 || mac.rfind("00:00:00", 0) == 0) {
      continue;
    }
    uint64_t hash = 1469598103934665603ULL;
    for (const char c : mac) {
      hash ^= static_cast<uint8_t>(c);
      hash *= 1099511628211ULL;
    }
    char buf[16];
    std::snprintf(buf, sizeof(buf), "SB-%06llx",
                  static_cast<unsigned long long>(hash & 0xFFFFFF));
    return buf;
  }
  return "SB-000000";
}


int StableObstacleId(const std::string &id) {
  unsigned h = 2166136261u;
  for (unsigned char c : id) {
    h ^= c;
    h *= 16777619u;
  }
  int v = static_cast<int>(h & 0x7fffffff);
  return v == 0 ? 1 : v;
}

perception::PerceptionObstacle::Type PerceptionTypeOf(const std::string &type) {
  if (type == "pedestrian") {
    return perception::PerceptionObstacle::PEDESTRIAN;
  }
  if (type == "static") {
    return perception::PerceptionObstacle::UNKNOWN_UNMOVABLE;
  }
  return perception::PerceptionObstacle::VEHICLE;
}

/** 通过 avahi-publish 做 mDNS 公告（不可用时静默跳过，仅影响跨设备发现） */
void AnnounceMdns(const std::string &service_id, int port) {
  const std::string instance = "simbridge-" + service_id.substr(3);
  const std::string cmd =
      "nohup avahi-publish -s " + instance + " _simbridge._tcp " +
      std::to_string(port) + " " + service_id + " >/dev/null 2>&1 &";
  std::system(cmd.c_str());
  AINFO << "mDNS announce requested: " << instance << "._simbridge._tcp";
}

const std::vector<SimBridge::ModuleDef> &ModuleTable() {
  static const std::vector<SimBridge::ModuleDef> table = {
      {"routing",
       {"modules/routing/dag/routing.dag",
        "modules/external_command/process_component/dag/"
        "external_command_process.dag"}},
      {"planning", {"modules/planning/planning_component/dag/planning.dag"}},
      {"prediction", {"modules/fake_prediction/dag/fake_prediction.dag"}},
      {"control", {"modules/control/control_component/dag/control.dag"}},
  };
  return table;
}

bool EndsWithAny(const std::string &file,
                 const std::vector<std::string> &candidates) {
  for (const auto &suffix : candidates) {
    const auto pos = file.find(suffix);
    if (pos != std::string::npos) {
      return true;
    }
  }
  return false;
}

}  // namespace

SimBridge::~SimBridge() { Stop(); }

bool SimBridge::Init() {
  if (!apollo::cyber::Init("worldsim")) {
    AERROR << "cyber init failed";
    return false;
  }
  node_ = apollo::cyber::CreateNode("worldsim");

  // 虚拟车由 SimPerfectControl 发布 localization + chassis
  localization_reader_ =
      node_->CreateReader<LocalizationEstimate>(FLAGS_localization_topic);
  // 场景障碍预定轨迹 → fake_prediction → /apollo/prediction
  gt_obstacles_writer_ =
      node_->CreateWriter<prediction::PredictionObstacles>(
          "/apollo/sim/gt_obstacles");
  // 算法模块输出
  planning_reader_ = node_->CreateReader<ADCTrajectory>(
      FLAGS_planning_trajectory_topic,
      [this](const std::shared_ptr<ADCTrajectory> &trajectory) {
        OnPlanning(trajectory);
      });
  planning_command_reader_ = node_->CreateReader<PlanningCommand>(
      FLAGS_planning_command,
      [this](const std::shared_ptr<PlanningCommand> &planning_command) {
        OnPlanningCommand(planning_command);
      });
  routing_response_reader_ = node_->CreateReader<RoutingResponse>(
      FLAGS_routing_response_topic,
      [this](const std::shared_ptr<RoutingResponse> &routing_response) {
        OnRoutingResponse(routing_response);
      });
  prediction_reader_ = node_->CreateReader<prediction::PredictionObstacles>(
      FLAGS_prediction_topic,
      [this](const std::shared_ptr<prediction::PredictionObstacles> &msg) {
        OnPrediction(msg);
      });
  control_reader_ = node_->CreateReader<control::ControlCommand>(
      FLAGS_control_command_topic,
      [this](const std::shared_ptr<control::ControlCommand> &cmd) {
        OnControlCommand(cmd);
      });

  // WebSocket 服务（CivetServer，同 dreamview_plus）
  websocket_ = std::make_shared<apollo::dreamview::WebSocketHandler>("worldsim");
  RegisterMessageHandlers();
  EnsureMapService();
  std::vector<std::string> server_options = {
      "document_root",         ".",
      "listening_ports",       std::to_string(FLAGS_sim_bridge_ws_port),
      "websocket_timeout_ms",  "600000",
      "request_timeout_ms",    "600000",
      "enable_keep_alive",     "yes",
      "tcp_nodelay",           "1",
      "keep_alive_timeout_ms", "500"};
  server_.reset(new CivetServer(server_options));
  server_->addWebSocketHandler("/ws", *websocket_);
  // 建连即推送 HmiStatus（mode/serviceId/模块状态）
  websocket_->RegisterConnectionReadyHandler([this](Connection *) {
    BroadcastHmiStatus();
  });

  // 唯一服务 ID（前端用它寻址，无需 IP）+ 可选 mDNS 公告
  service_id_ = FLAGS_sim_bridge_service_id;
  if (service_id_.empty()) {
    service_id_ = DeriveServiceId();
  }
  if (FLAGS_sim_bridge_mdns) {
    AnnounceMdns(service_id_, FLAGS_sim_bridge_ws_port);
  }
  current_vehicle_ = ReadCurrentProfile();
  AINFO << "SimBridge service id: " << service_id_
        << " current profile: " << current_vehicle_;
  AINFO << "SimBridge listening on ws://localhost:"
        << FLAGS_sim_bridge_ws_port << "/ws";
  return true;
}

void SimBridge::Start() {
  sim_timer_.reset(new cyber::Timer(
      kSimIntervalMs, [this]() { RunOnce(); }, false));
  fallback_timer_.reset(new cyber::Timer(
      kFallbackIntervalMs,
      [this]() {
        PublishGtObstacles();
      },
      false));
  sim_timer_->Start();
  fallback_timer_->Start();
}

void SimBridge::Stop() {
  StopSimControl();
  if (sim_timer_) {
    sim_timer_->Stop();
    sim_timer_.reset();
  }
  if (fallback_timer_) {
    fallback_timer_->Stop();
    fallback_timer_.reset();
  }
  if (server_) {
    server_.reset();
  }
}

void SimBridge::RegisterMessageHandlers() {
  websocket_->RegisterMessageHandler(
      "GetVehicleList",
      [this](const Json &json, Connection *conn) {
        HandleGetVehicleList(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "GetMapList", [this](const Json &json, Connection *conn) {
        HandleGetMapList(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "GetVehicleParam", [this](const Json &json, Connection *conn) {
        HandleGetVehicleParam(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "SetVehicle", [this](const Json &json, Connection *conn) {
        HandleSetVehicle(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "SetMap", [this](const Json &json, Connection *conn) {
        HandleSetMap(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "GetMapElements", [this](const Json &json, Connection *conn) {
        HandleGetMapElements(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "SetModules", [this](const Json &json, Connection *conn) {
        HandleSetModules(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "SimControl", [this](const Json &json, Connection *conn) {
        HandleSimControl(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "SendRouting", [this](const Json &json, Connection *conn) {
        HandleSendRouting(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "PreviewRouting", [this](const Json &json, Connection *conn) {
        HandlePreviewRouting(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "UploadObstacles", [this](const Json &json, Connection *conn) {
        HandleUploadObstacles(json, conn);
      });
  websocket_->RegisterMessageHandler(
      "LoadScenario", [this](const Json &json, Connection *conn) {
        HandleLoadScenario(json, conn);
      });
  // 长链接保活：前端 10s 心跳，30s 无消息即重连
  websocket_->RegisterMessageHandler(
      "Ping", [this](const Json &json, Connection *conn) {
        Reply(json, conn, Json{{"t", json["data"].value("t", 0)}});
        websocket_->SendData(conn,
                             Json{{"type", "Pong"}, {"data", Json::object()}}.dump());
      });
  // 部分客户端会回发 type=Pong；忽略即可，避免刷屏 No message handler
  websocket_->RegisterMessageHandler(
      "Pong", [](const Json &, Connection *) {});
}

void SimBridge::Reply(const Json &request, Connection *conn,
                      const Json &payload) {
  Json reply;
  reply["type"] = request.value("type", "Unknown");
  Json data = payload;
  if (request.contains("data") && request["data"].contains("requestId")) {
    data["requestId"] = request["data"]["requestId"];
  }
  reply["data"] = data;
  websocket_->SendData(conn, reply.dump());
}

void SimBridge::BroadcastJson(const Json &json) {
  websocket_->BroadcastData(json.dump());
}

void SimBridge::BroadcastRuntimeLog(const std::string &level,
                                    const std::string &message) {
  BroadcastJson(
      Json{{"type", "RuntimeLog"},
           {"data", Json{{"level", level}, {"message", message}}}});
}

void SimBridge::BroadcastHmiStatus() {
  Json data;
  data["simRunning"] = sim_running_;
  Json modules;
  for (const auto &def : ModuleTable()) {
    auto it = desired_modules_.find(def.name);
    modules[def.name] = it != desired_modules_.end() && it->second;
  }
  data["modules"] = modules;
  data["currentVehicle"] = current_vehicle_;
  data["currentMap"] = current_map_;
  data["obstacleCount"] = static_cast<int64_t>(uploaded_obstacles_.size());
  data["serviceId"] = service_id_;
  data["mode"] = "apollo";
  BroadcastJson(Json{{"type", "HmiStatus"}, {"data", data}});
}

void SimBridge::EnsureMapService() {
  if (!map_service_) {
    map_service_ = std::make_unique<MapService>(false);
  } else {
    map_service_->ReloadMap(true);
  }
}

void SimBridge::SnapStartPoseToLaneLocked() {
  // caller holds mutex_
  // 当前 START / SendRouting 已停用本函数，保留场景 JSON 编辑位姿。
  EnsureMapService();
  if (!map_service_) {
    return;
  }

  apollo::common::PointENU point;
  point.set_x(start_x_);
  point.set_y(start_y_);
  apollo::hdmap::LaneInfoConstPtr lane;
  double s = 0.0;
  double l = 0.0;
  constexpr double kSearchRadius = 8.0;
  constexpr double kMaxHeadingDiff = M_PI / 2;
  const auto &hdmap = apollo::hdmap::HDMapUtil::BaseMap();
  bool snapped = false;
  if (hdmap.GetNearestLaneWithHeading(point, kSearchRadius, start_heading_,
                                      kMaxHeadingDiff, &lane, &s, &l) == 0 &&
      lane) {
    const auto smooth = lane->GetSmoothPoint(s);
    start_x_ = smooth.x();
    start_y_ = smooth.y();
    start_heading_ = lane->Heading(s);
    snapped = true;
  }
  if (!snapped) {
    double theta = start_heading_;
    double s_pose = 0.0;
    if (map_service_->GetPoseWithRegardToLane(start_x_, start_y_, &theta,
                                              &s_pose)) {
      start_heading_ = theta;
    }
  }

  if (sim_running_) {
    ResetSimControlPose(start_x_, start_y_, start_heading_);
  }
  BroadcastRuntimeLog(
      "info",
      "起点已吸附车道 x=" + std::to_string(start_x_) +
          " y=" + std::to_string(start_y_) +
          " heading=" + std::to_string(start_heading_));
}

void SimBridge::FailSimulationDueToRouting(const std::string &reason) {
  {
    std::lock_guard<std::mutex> lock(mutex_);
    sim_running_ = false;
    has_destination_ = false;
    arrive_hold_frames_ = 0;
    received_planning_ = false;
    for (auto &kv : desired_modules_) {
      kv.second = false;
    }
  }
  StopSimControl();
  BroadcastRuntimeLog("error", "routing 失败，仿真已停止：" + reason);
  BroadcastJson(Json{{"type", "SimEvent"},
                     {"data",
                      {{"event", "routing_failed"},
                       {"message", reason}}}});
  BroadcastHmiStatus();
  StopRunningModules();
}

void SimBridge::BroadcastRoutingPathFromResponse(
    const RoutingResponse &routing_response) {
  if (!map_service_ || routing_response.road_size() == 0) {
    return;
  }
  const double ts = routing_response.header().timestamp_sec();
  if (last_routing_broadcast_time_ == ts) {
    return;
  }
  std::vector<apollo::hdmap::Path> paths;
  if (!map_service_->GetPathsFromRouting(routing_response, &paths)) {
    AWARN << "GetPathsFromRouting failed";
    return;
  }

  Json data;
  data["routingTime"] = ts;
  if (routing_response.has_measurement()) {
    data["distance"] = routing_response.measurement().distance();
  }
  Json route_path = Json::array();
  for (const auto &path : paths) {
    Json passage;
    Json points = Json::array();
    for (const auto &pp : path.path_points()) {
      points.push_back(
          Json{{"x", pp.x() + map_service_->GetXOffset()},
               {"y", pp.y() + map_service_->GetYOffset()},
               {"z", 0.0}});
    }
    passage["point"] = points;
    route_path.push_back(passage);
  }
  data["routePath"] = route_path;
  last_routing_broadcast_time_ = ts;
  AINFO << "Broadcast RoutingPath passages=" << route_path.size();
  BroadcastJson(Json{{"type", "RoutingPath"}, {"data", data}});
}

void SimBridge::BroadcastRoutingPath(
    const PlanningCommand &planning_command) {
  if (!planning_command.has_lane_follow_command()) {
    return;
  }
  BroadcastRoutingPathFromResponse(planning_command.lane_follow_command());
}

void SimBridge::PublishPoseAt(double x, double y, double z, double heading,
                              int frames) {
  ResetSimControlPose(x, y, heading);
  if (frames > 0) {
    std::this_thread::sleep_for(std::chrono::milliseconds(frames * 20));
  }
  BroadcastEgoState(x, y, z, heading, 0.0);
}

void SimBridge::EnsureSimControlStarted() {
  auto *mgr = SimControlManager::Instance();
  if (sim_control_model_started_) {
    return;
  }
  if (!mgr->IsEnabled()) {
    mgr->Start();
  }
  if (mgr->ChangeDynamicModel(FLAGS_sim_perfect_control)) {
    sim_control_model_started_ = true;
  } else {
    AERROR << "Failed to start SimPerfectControl dynamic model";
  }
}

void SimBridge::StopSimControl() {
  auto *mgr = SimControlManager::Instance();
  if (mgr->IsEnabled()) {
    mgr->Stop();
  }
  sim_control_model_started_ = false;
}

void SimBridge::ResetSimControlPose(double x, double y, double heading) {
  EnsureSimControlStarted();
  SimControlManager::Instance()->ReSetPoinstion(x, y, heading);
}

void SimBridge::BroadcastEgoState(double x, double y, double z, double heading,
                                  double speed) {
  Json data;
  data["x"] = x;
  data["y"] = y;
  data["z"] = z;
  data["heading"] = heading;
  data["speed"] = speed;
  data["timestampSec"] = Clock::NowInSeconds();
  BroadcastJson(Json{{"type", "EgoState"}, {"data", data}});
}

void SimBridge::BroadcastAgentsState() {
  if (world_ == nullptr) {
    return;
  }
  const auto agents = world_->SnapshotAgents();
  Json arr = Json::array();
  for (const auto& a : agents) {
    std::string type = "vehicle";
    switch (a.type) {
      case AGENT_TYPE_PEDESTRIAN:
        type = "pedestrian";
        break;
      case AGENT_TYPE_STATIC:
      case AGENT_TYPE_LOADER:
        type = "static";
        break;
      case AGENT_TYPE_BICYCLE:
        type = "vehicle";
        break;
      case AGENT_TYPE_TRUCK:
        type = "vehicle";
        break;
      default:
        type = "vehicle";
        break;
    }
    arr.push_back(Json{
        {"id", a.id},
        {"type", type},
        {"x", a.x},
        {"y", a.y},
        {"z", a.z},
        {"heading", a.heading},
        {"speed", a.speed},
        {"moving", a.moving},
        {"enabled", a.enabled},
    });
  }
  BroadcastJson(Json{{"type", "AgentsState"},
                     {"data",
                      {{"agents", arr},
                       {"timestampSec", Clock::NowInSeconds()}}}});
}

// ---------------- 车辆 Profile / 地图服务 ----------------

namespace {

std::string RunShellCapture(const std::string &cmd) {
  FILE *pipe = popen(cmd.c_str(), "r");
  if (pipe == nullptr) {
    return "";
  }
  std::string out;
  char buf[512];
  while (fgets(buf, sizeof(buf), pipe) != nullptr) {
    out += buf;
  }
  pclose(pipe);
  return out;
}

constexpr char kApolloNeoSetup[] =
    "cd /apollo_workspace && . /opt/apollo/neo/setup.sh";

std::string WrapEmShell(const std::string &inner) {
  return std::string("bash -lc '") + kApolloNeoSetup + " && " + inner + "'";
}

int RunEmShell(const std::string &inner) {
  return std::system(WrapEmShell(inner).c_str());
}

std::string RunEmCapture(const std::string &inner) {
  return RunShellCapture(WrapEmShell(inner));
}

Json ParseEmProfileList() {
  // cyber 常把 SIGCHLD 设为忽略，pclose/waitpid 可能失败；只要 stdout 有 JSON 就采用。
  FILE *pipe = popen(WrapEmShell("em profile list 2>/dev/null").c_str(), "r");
  if (pipe == nullptr) {
    return Json();
  }
  std::string out;
  char buf[512];
  while (fgets(buf, sizeof(buf), pipe) != nullptr) {
    out += buf;
  }
  const int rc = pclose(pipe);
  if (out.empty()) {
    AWARN << "em profile list produced empty output, pclose_rc=" << rc;
    return Json();
  }
  try {
    return Json::parse(out);
  } catch (const std::exception &e) {
    AWARN << "em profile list JSON parse failed: " << e.what()
          << " raw=" << out.substr(0, 120);
    return Json();
  } catch (...) {
    return Json();
  }
}

std::vector<std::string> ScanProfileDirs(const std::filesystem::path &root) {
  std::vector<std::string> profiles;
  std::error_code ec;
  if (!std::filesystem::exists(root, ec)) {
    return profiles;
  }
  for (const auto &entry : std::filesystem::directory_iterator(root, ec)) {
    if (ec) {
      AERROR << "ScanProfileDirs iter failed: " << root << " " << ec.message();
      break;
    }
    std::error_code ec2;
    if (!entry.is_directory(ec2) && !entry.is_symlink(ec2)) {
      continue;
    }
    const std::string name = entry.path().filename().string();
    if (name.empty() || name == "current" || name[0] == '.') {
      continue;
    }
    profiles.push_back(name);
  }
  return profiles;
}

std::string EmServiceName(const std::string &name) {
  if (name == "routing") {
    return "external_command";
  }
  if (name == "prediction") {
    return "fake_prediction";
  }
  return name;
}

std::string TailFileLines(const std::string &path, int max_lines) {
  std::ifstream in(path);
  if (!in.is_open()) {
    return "";
  }
  std::vector<std::string> lines;
  std::string line;
  while (std::getline(in, line)) {
    lines.push_back(line);
    if (static_cast<int>(lines.size()) > max_lines) {
      lines.erase(lines.begin());
    }
  }
  std::string out;
  for (const auto &l : lines) {
    if (!out.empty()) {
      out += '\n';
    }
    out += l;
  }
  return out;
}

bool EmStatusRunning(const std::string &status_out) {
  return status_out.find("RUNNING") != std::string::npos &&
         status_out.find("ERROR") == std::string::npos;
}

}  // namespace

void SimBridge::EnsureRoutingModuleForPreview() {
  const std::string cmd =
      "em start external_command >> /apollo/data/log/em_external_command.out "
      "2>&1";
  AINFO << "PreviewRouting: ensure external_command via em";
  RunEmShell(cmd);
  std::this_thread::sleep_for(std::chrono::milliseconds(1200));
}

void SimBridge::EnsurePlanningLaneFollowConf() {
  const std::filesystem::path stage_dir =
      "/apollo/modules/planning/scenarios/lane_follow/conf/lane_follow_stage";
  static const std::pair<const char *, const char *> kLinks[] = {
      {"lane_change_path.pb.txt", "lane_change_path"},
      {"lane_follow_path.pb.txt", "lane_follow_path"},
      {"lane_borrow_path.pb.txt", "lane_borrow_path"},
      {"fallback_path.pb.txt", "fallback_path"},
      {"path_decider.pb.txt", "path_decider"},
      {"rule_based_stop_decider.pb.txt", "rule_based_stop_decider"},
      {"speed_bounds_priori_decider.pb.txt", "speed_bounds_decider"},
      {"speed_heuristic_optimizer.pb.txt", "path_time_heuristic"},
      {"speed_decider.pb.txt", "speed_decider"},
      {"speed_bounds_final_decider.pb.txt", "speed_bounds_decider"},
      {"piecewise_jerk_speed.pb.txt", "piecewise_jerk_speed"},
  };
  const std::vector<std::filesystem::path> task_roots = {
      "/opt/apollo/neo/share/modules/planning/tasks",
      "/opt/apollo/neo/src/modules/planning/tasks",
  };
  std::error_code ec;
  std::filesystem::create_directories(stage_dir, ec);
  int linked = 0;
  for (const auto &entry : kLinks) {
    const std::filesystem::path dst = stage_dir / entry.first;
    if (std::filesystem::exists(dst, ec)) {
      ++linked;
      continue;
    }
    std::filesystem::path src;
    for (const auto &root : task_roots) {
      const auto candidate =
          root / entry.second / "conf" / "default_conf.pb.txt";
      if (std::filesystem::exists(candidate, ec)) {
        src = candidate;
        break;
      }
    }
    if (src.empty()) {
      AWARN << "EnsurePlanningLaneFollowConf: missing default conf for "
            << entry.first;
      continue;
    }
    std::filesystem::create_symlink(src, dst, ec);
    if (ec) {
      AWARN << "EnsurePlanningLaneFollowConf: symlink failed " << dst << " -> "
            << src << ": " << ec.message();
      continue;
    }
    ++linked;
    AINFO << "Planning conf linked: " << dst << " -> " << src;
  }
  BroadcastRuntimeLog(
      "info",
      "planning: lane_follow_stage 配置 " + std::to_string(linked) + "/" +
          std::to_string(sizeof(kLinks) / sizeof(kLinks[0])) + " 就绪");
}

std::vector<std::string> SimBridge::ScanProfiles() const {
  std::vector<std::string> profiles;
  // 优先走 em（与 `em profile use` 同源）。
  const Json listed = ParseEmProfileList();
  if (listed.contains("profiles") && listed["profiles"].is_array()) {
    for (const auto &item : listed["profiles"]) {
      if (item.is_string()) {
        profiles.push_back(item.get<std::string>());
      }
    }
  }

  // 目录扫描兜底（含绝对路径），避免 popen/SIGCHLD 导致空列表。
  std::error_code ec;
  std::vector<std::filesystem::path> roots;
  if (listed.contains("root") && listed["root"].is_string()) {
    roots.emplace_back(listed["root"].get<std::string>());
  }
  std::filesystem::path configured = FLAGS_sim_bridge_profiles_path;
  if (configured.is_relative()) {
    roots.push_back(std::filesystem::current_path(ec) / configured);
  } else {
    roots.push_back(configured);
  }
  roots.emplace_back("/apollo_workspace/profiles");
  roots.emplace_back("/apollo/profiles");

  for (const auto &root : roots) {
    auto scanned = ScanProfileDirs(root);
    profiles.insert(profiles.end(), scanned.begin(), scanned.end());
  }

  std::sort(profiles.begin(), profiles.end());
  profiles.erase(std::unique(profiles.begin(), profiles.end()), profiles.end());
  if (profiles.empty()) {
    AERROR << "ScanProfiles empty after em+dirs; cwd="
           << std::filesystem::current_path(ec);
  } else {
    AINFO << "ScanProfiles found " << profiles.size() << " profiles";
  }
  return profiles;
}

std::string SimBridge::ReadCurrentProfile() const {
  const Json listed = ParseEmProfileList();
  if (listed.contains("current") && listed["current"].is_string()) {
    return listed["current"].get<std::string>();
  }
  std::error_code ec;
  std::filesystem::path current =
      std::filesystem::path(FLAGS_sim_bridge_profiles_path) / "current";
  if (current.is_relative()) {
    current = std::filesystem::current_path(ec) / current;
  }
  if (!std::filesystem::exists(current, ec)) {
    return "";
  }
  const auto resolved = std::filesystem::canonical(current, ec);
  if (ec) {
    return "";
  }
  return resolved.filename().string();
}

bool ResolveVehicleConfigPath(std::string *out) {
  const std::vector<std::string> candidates = {
      FLAGS_sim_bridge_global_vehicle_config,
      "/apollo/modules/common/data/vehicle_param.pb.txt",
      "/apollo_workspace/modules/common/data/vehicle_param.pb.txt",
      "modules/common/data/vehicle_param.pb.txt",
  };
  std::error_code ec;
  for (const auto &c : candidates) {
    std::filesystem::path p = c;
    if (p.is_relative()) {
      p = std::filesystem::current_path(ec) / p;
    }
    if (std::filesystem::exists(p, ec)) {
      *out = p.string();
      return true;
    }
  }
  return false;
}

/** profiles/<profile>/modules/common/data/vehicle_param.pb.txt */
bool ResolveProfileVehicleConfigPath(const std::string &profile,
                                     std::string *out) {
  if (profile.empty() || out == nullptr) {
    return false;
  }
  std::error_code ec;
  std::vector<std::filesystem::path> roots;
  std::filesystem::path configured = FLAGS_sim_bridge_profiles_path;
  if (configured.is_relative()) {
    roots.push_back(std::filesystem::current_path(ec) / configured);
  } else {
    roots.push_back(configured);
  }
  roots.emplace_back("/apollo_workspace/profiles");
  roots.emplace_back("/apollo/profiles");

  for (const auto &root : roots) {
    const auto p =
        root / profile / "modules" / "common" / "data" / "vehicle_param.pb.txt";
    if (std::filesystem::exists(p, ec)) {
      *out = p.string();
      return true;
    }
  }
  return false;
}

bool SimBridge::ReloadVehicleConfig() {
  return ReloadVehicleConfigForProfile(current_vehicle_);
}

bool SimBridge::ReloadVehicleConfigForProfile(const std::string &profile) {
  std::string config_path;
  // 选中车型必须用该 profile 下的车参，避免全局 modules/common/... 残留旧尺寸
  if (!ResolveProfileVehicleConfigPath(profile, &config_path)) {
    if (!ResolveVehicleConfigPath(&config_path)) {
      AERROR << "vehicle_param.pb.txt not found (profile=" << profile << ")";
      return false;
    }
    AWARN << "profile vehicle_param missing for " << profile
          << " — fallback global: " << config_path;
  }
  FLAGS_vehicle_config_path = config_path;
  AINFO << "Reload vehicle config: " << config_path
        << " (profile=" << profile << ")";
  common::VehicleConfigHelper::Instance()->Init();
  return true;
}

bool SimBridge::ApplyProfile(const std::string &profile) {
  if (profile.empty()) {
    return false;
  }
  const std::string already = ReadCurrentProfile();
  if (already == profile) {
    AINFO << "profile already active: " << profile
          << " — skip em profile use, reload profile vehicle_param";
    current_vehicle_ = profile;
    return ReloadVehicleConfigForProfile(profile);
  }
  const auto profiles = ScanProfiles();
  if (std::find(profiles.begin(), profiles.end(), profile) == profiles.end()) {
    AERROR << "profile not found: " << profile;
    return false;
  }

  const std::string cmd = "em profile use " + ShellQuote(profile) +
                          " >> /apollo/data/log/em_profile.out 2>&1";
  AINFO << "Apply profile: " << cmd;
  const int rc = RunEmShell(cmd);
  const int exit_code = WEXITSTATUS(rc);
  const std::string now = ReadCurrentProfile();
  if (rc != 0 && now != profile) {
    AERROR << "em profile use failed, rc=" << rc << " exit=" << exit_code
           << " profile=" << profile << " current=" << now;
    return false;
  }
  if (rc != 0 && now == profile) {
    // 常见：overlay 部分文件 Permission denied，但 current 已切过去
    AWARN << "em profile use exit=" << exit_code
          << " but current already " << profile << " — continue";
  }
  current_vehicle_ = profile;
  // 即使 em 已切换，仍直接读 profile 目录，避免全局 vehicle_param 未覆盖成功
  return ReloadVehicleConfigForProfile(profile);
}

std::vector<std::string> SimBridge::ScanMapDirs() const {
  std::vector<std::string> maps;
  std::error_code ec;
  std::vector<std::filesystem::path> candidates;
  std::filesystem::path configured = FLAGS_sim_bridge_maps_data_path;
  if (configured.is_relative()) {
    candidates.push_back(std::filesystem::current_path(ec) / configured);
  } else {
    candidates.push_back(configured);
  }
  candidates.emplace_back("/apollo/modules/map/data");
  candidates.emplace_back("/apollo_workspace/modules/map/data");

  for (const auto &root : candidates) {
    if (!std::filesystem::exists(root, ec)) {
      continue;
    }
    for (const auto &entry : std::filesystem::directory_iterator(root, ec)) {
      if (entry.is_directory(ec)) {
        maps.push_back(entry.path().filename().string());
      }
    }
    if (!maps.empty()) {
      break;
    }
  }
  std::sort(maps.begin(), maps.end());
  maps.erase(std::unique(maps.begin(), maps.end()), maps.end());
  return maps;
}

std::string SimBridge::ResolveMapFile(const std::string &map_name) const {
  const std::string base = FLAGS_sim_bridge_maps_data_path + "/" + map_name;
  // 展示优先 sim_map（降采样），否则 base_map；bin/txt 皆可
  for (const auto *file :
       {"sim_map.bin", "sim_map.txt", "base_map.bin", "base_map.txt"}) {
    const std::string candidate = base + "/" + file;
    if (std::filesystem::exists(candidate)) {
      return candidate;
    }
  }
  return "";
}

void SimBridge::HandleGetVehicleList(const Json &json, Connection *conn) {
  Json data;
  data["vehicles"] = ScanProfiles();
  const std::string current = ReadCurrentProfile();
  if (!current.empty()) {
    current_vehicle_ = current;
    // 列表刷新时同步车参，避免「当前=ranger 但尺寸仍是全局默认」
    ReloadVehicleConfigForProfile(current);
  }
  data["current"] = current_vehicle_;
  Reply(json, conn, data);
}

void SimBridge::HandleGetMapList(const Json &json, Connection *conn) {
  Json data;
  data["maps"] = ScanMapDirs();
  Reply(json, conn, data);
}

Json SimBridge::VehicleParamJson() const {
  const auto &config = common::VehicleConfigHelper::Instance()->GetConfig();
  return JsonUtil::ProtoToJson(config.vehicle_param());
}

void SimBridge::HandleGetVehicleParam(const Json &json, Connection *conn) {
  const std::string profile =
      !current_vehicle_.empty() ? current_vehicle_ : ReadCurrentProfile();
  if (!profile.empty()) {
    current_vehicle_ = profile;
    ReloadVehicleConfigForProfile(profile);
  }
  Json data = VehicleParamJson();
  Reply(json, conn, data);
}

void SimBridge::HandleSetVehicle(const Json &json, Connection *conn) {
  const std::string vehicle = json["data"].value("vehicle", "");
  if (!ApplyProfile(vehicle)) {
    Json err{{"message", "em profile use failed: " + vehicle}};
    Reply(json, conn, err);
    return;
  }
  BroadcastJson(Json{{"type", "VehicleParam"}, {"data", VehicleParamJson()}});
  BroadcastHmiStatus();
  Reply(json, conn, Json{{"ok", true}, {"profile", current_vehicle_}});
}

void SimBridge::HandleSetMap(const Json &json, Connection *conn) {
  const std::string map = json["data"].value("map", "");
  const std::string map_dir = FLAGS_sim_bridge_maps_data_path + "/" + map;
  if (!std::filesystem::exists(map_dir)) {
    Json err{{"message", "map not found: " + map}};
    Reply(json, conn, err);
    return;
  }
  FLAGS_map_dir = map_dir;
  current_map_ = map;
  EnsureMapService();
  // routing/planning 进程内缓存了旧地图，切图后需重启模块
  BroadcastHmiStatus();
  Reply(json, conn, Json::object());
}

void SimBridge::HandleGetMapElements(const Json &json, Connection *conn) {
  const std::string map = json["data"].value("map", current_map_);
  const std::string map_file = ResolveMapFile(map);
  if (map_file.empty()) {
    Json err{{"message", "map file not found for: " + map}};
    Reply(json, conn, err);
    return;
  }
  hdmap::Map map_proto;
  if (!cyber::common::GetProtoFromFile(map_file, &map_proto)) {
    Json err{{"message", "failed to parse map file: " + map_file}};
    Reply(json, conn, err);
    return;
  }
  Json data;
  data["map"] = JsonUtil::ProtoToJson(map_proto);
  data["name"] = map;
  Reply(json, conn, data);
}

// ---------------- 模块管理 ----------------

void SimBridge::HandleSetModules(const Json &json, Connection *conn) {
  const Json &modules = json["data"].contains("modules")
                            ? json["data"]["modules"]
                            : Json::object();
  {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto &def : ModuleTable()) {
      if (modules.contains(def.name)) {
        desired_modules_[def.name] = modules[def.name].get<bool>();
      } else if (!desired_modules_.count(def.name)) {
        desired_modules_[def.name] = false;
      }
    }
  }
  Reply(json, conn, Json::object());
  // 勾选仅记录期望状态；模块在 SimControl START 时才真正拉起
  BroadcastHmiStatus();
}

void SimBridge::StartStopModules() {
  std::lock_guard<std::mutex> module_lock(module_ops_mutex_);
  std::unordered_map<std::string, bool> desired;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    desired = desired_modules_;
  }
  const bool planning_requested =
      desired.count("planning") != 0 && desired.at("planning");

  // 通过 em（smrtd）启停。前端勾选名 → em 服务名：
  //   routing    → external_command
  //   prediction → fake_prediction（场景真值模块；不启 perception / prediction）
  // planning 的 mainboard 已捆绑 external_command_process.dag，
  // 不可与 routing 单独拉起的 external_command 并存。
  for (const auto &def : ModuleTable()) {
    const auto it = desired.find(def.name);
    const bool should_run = it != desired.end() && it->second;
    const std::string svc = EmServiceName(def.name);

    if (def.name == "routing" && should_run && planning_requested) {
      BroadcastRuntimeLog(
          "info",
          "routing: 跳过单独启动 external_command（已由 planning mainboard 捆绑）");
      continue;
    }

    if (def.name == "prediction" && should_run) {
      // 避免官方 prediction 抢占 /apollo/prediction
      RunEmShell("em stop prediction >> /apollo/data/log/em_prediction.out "
                 "2>&1");
      RunEmShell("em stop perception >> /apollo/data/log/em_perception.out "
                 "2>&1");
    }

    if (def.name == "planning" && should_run) {
      EnsurePlanningLaneFollowConf();
      BroadcastRuntimeLog(
          "info",
          "planning: 停止独立 external_command，避免与 planning mainboard 冲突");
      RunEmShell("em stop external_command >> "
                 "/apollo/data/log/em_external_command.out 2>&1");
      std::this_thread::sleep_for(std::chrono::milliseconds(800));
    }

    const std::string log = "/apollo/data/log/em_" + svc + ".out";
    if (should_run) {
      const std::string stop_cmd = "em stop " + svc + " >> " + log + " 2>&1";
      RunEmShell(stop_cmd);
      std::this_thread::sleep_for(std::chrono::milliseconds(800));
    }
    const std::string cmd = std::string("em ") + (should_run ? "start " : "stop ") +
                            svc + " >> " + log + " 2>&1";
    AINFO << (should_run ? "Start" : "Stop") << " module via em: " << cmd;
    BroadcastRuntimeLog("info",
                        std::string(should_run ? "启动模块: " : "停止模块: ") +
                            def.name + " (" + svc + ")");
    RunEmShell(cmd);
    if (should_run) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1500));
      const std::string status_out = RunEmCapture("em status " + svc + " 2>&1");
      const bool running = EmStatusRunning(status_out);
      if (running) {
        BroadcastRuntimeLog("info", def.name + ": RUNNING");
      } else {
        const std::string tail = TailFileLines(log, 12);
        std::string msg = def.name + ": 启动失败";
        if (!status_out.empty()) {
          msg += " — " + status_out;
        }
        if (!tail.empty()) {
          msg += "\n" + tail;
        }
        BroadcastRuntimeLog("error", msg);
        AERROR << msg;
      }
    }
  }
}

void SimBridge::StopRunningModules() {
  std::lock_guard<std::mutex> module_lock(module_ops_mutex_);
  for (const auto &def : ModuleTable()) {
    const std::string svc = EmServiceName(def.name);
    const std::string log = "/apollo/data/log/em_" + svc + ".out";
    const std::string cmd =
        "em stop " + svc + " >> " + log + " 2>&1";
    AINFO << "Force stop module via em: " << cmd;
    RunEmShell(cmd);
  }
}

// ---------------- 仿真控制 ----------------

void SimBridge::HandleSimControl(const Json &json, Connection *conn) {
  const Json &data = json["data"];
  const std::string action = data.value("action", "");
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (action == "START") {
      // 前端在 START 时附带最终模块勾选（scenario.simConfig）
      if (data.contains("modules") && data["modules"].is_object()) {
        for (const auto &def : ModuleTable()) {
          if (data["modules"].contains(def.name)) {
            desired_modules_[def.name] = data["modules"][def.name].get<bool>();
          }
        }
      }
      if (data.contains("startPoint")) {
        const Json &sp = data["startPoint"];
        start_x_ = sp.value("x", 0.0);
        start_y_ = sp.value("y", 0.0);
        start_z_ = sp.value("z", 0.0);
        start_heading_ = sp.value("heading", 0.0);
      }
      // 保留场景 JSON / 前端编辑的位姿与 heading，不做车道中心线吸附。
      // SnapStartPoseToLaneLocked();
      received_planning_ = false;
      received_prediction_ = false;
      has_destination_ = false;
      arrive_hold_frames_ = 0;
      sim_running_ = true;
      empty_planning_warned_ = false;
    } else if (action == "STOP") {
      sim_running_ = false;
      received_prediction_ = false;
      has_destination_ = false;
      arrive_hold_frames_ = 0;
      last_routing_broadcast_time_ = -1.0;
      for (auto &kv : desired_modules_) {
        kv.second = false;
      }
    } else if (action == "RESET") {
      // 优先用前端下发的场景起点；否则回退到上次 START 记录的 start_*。
      if (data.contains("startPoint")) {
        const Json &sp = data["startPoint"];
        start_x_ = sp.value("x", start_x_);
        start_y_ = sp.value("y", start_y_);
        start_z_ = sp.value("z", start_z_);
        start_heading_ = sp.value("heading", start_heading_);
      }
      sim_running_ = false;
      received_planning_ = false;
      received_prediction_ = false;
      has_destination_ = false;
      arrive_hold_frames_ = 0;
      for (auto &kv : desired_modules_) {
        kv.second = false;
      }
      current_trajectory_.Clear();
      BroadcastEgoState(start_x_, start_y_, start_z_, start_heading_, 0.0);
    }
  }
  BroadcastHmiStatus();
  // START：严格串行「应用配置 → 启动模块」，完成后再 Reply；
  // 前端收到 Reply 后再下发路由，避免模块未就绪。
  if (action == "START") {
    std::thread([this, json, conn]() {
      std::string profile;
      {
        std::lock_guard<std::mutex> lock(mutex_);
        profile = current_vehicle_;
      }
      if (profile.empty()) {
        profile = ReadCurrentProfile();
      }
      bool profile_ok = true;
      if (!profile.empty()) {
        profile_ok = ApplyProfile(profile);
        if (profile_ok) {
          AINFO << "START: applied profile " << profile;
          BroadcastJson(
              Json{{"type", "VehicleParam"}, {"data", VehicleParamJson()}});
        } else {
          AERROR << "START: failed to apply profile " << profile;
        }
      }
      // 配置之后再拉起模块（routing→external_command 等）
      StartStopModules();
      // 启动 dreamview SimPerfectControl 并对齐场景起点
      {
        double x = 0.0;
        double y = 0.0;
        double z = 0.0;
        double heading = 0.0;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          x = start_x_;
          y = start_y_;
          z = start_z_;
          heading = start_heading_;
        }
        PublishPoseAt(x, y, z, heading, 5);
      }
      BroadcastHmiStatus();
      Reply(json, conn,
            Json{{"ok", profile_ok},
                 {"ready", true},
                 {"profile", profile},
                 {"message", profile_ok ? "profile+modules ready"
                                        : "modules started; profile apply failed"}});
    }).detach();
    return;
  }

  Reply(json, conn, Json::object());
  if (action == "STOP" || action == "RESET") {
    StopSimControl();
    StopRunningModules();
    BroadcastHmiStatus();
  }
}

Json SimBridge::ExecuteLaneFollowRouting(const Json &points, double sx,
                                         double sy, double sh) {
  if (!points.is_array() || points.empty()) {
    return Json{{"ok", false},
                {"accepted", false},
                {"message", "routing needs >= 1 points"}};
  }

  const double raw_ex = points.back().value("x", 0.0);
  const double raw_ey = points.back().value("y", 0.0);

  auto lane_follow_client =
      node_->CreateClient<LaneFollowCommand, CommandStatus>(
          "/apollo/external_command/lane_follow");

  LaneFollowCommand command;
  FillHeader("worldsim", &command);
  command.set_command_id(++obstacle_seq_);
  // 场景放置点 = routing 起点（不用 localization 隐式起点）
  command.set_is_start_pose_set(true);
  auto *start_wp = command.add_way_point();
  start_wp->set_x(sx);
  start_wp->set_y(sy);
  start_wp->set_heading(sh);

  const size_t n = points.size();
  for (size_t i = 0; i + 1 < n; ++i) {
    const double x = points[i].value("x", 0.0);
    const double y = points[i].value("y", 0.0);
    if (std::hypot(x - sx, y - sy) < 2.0) {
      continue;
    }
    if (std::hypot(x - raw_ex, y - raw_ey) < 2.0) {
      continue;
    }
    auto *pose = command.add_way_point();
    pose->set_x(x);
    pose->set_y(y);
    if (points[i].contains("heading") && points[i]["heading"].is_number()) {
      pose->set_heading(points[i]["heading"].get<double>());
    } else {
      const double nx =
          (i + 1 < n) ? points[i + 1].value("x", raw_ex) : raw_ex;
      const double ny =
          (i + 1 < n) ? points[i + 1].value("y", raw_ey) : raw_ey;
      pose->set_heading(HeadingToward(x, y, nx, ny));
      AWARN << "SendRouting point[" << i << "] missing heading, inferred from geometry";
    }
  }

  auto *end_pose = command.mutable_end_pose();
  end_pose->set_x(raw_ex);
  end_pose->set_y(raw_ey);
  if (points.back().contains("heading") &&
      points.back()["heading"].is_number()) {
    end_pose->set_heading(points.back()["heading"].get<double>());
  } else {
    double px = sx;
    double py = sy;
    if (n >= 2) {
      px = points[n - 2].value("x", sx);
      py = points[n - 2].value("y", sy);
    }
    end_pose->set_heading(HeadingToward(px, py, raw_ex, raw_ey));
    AWARN << "SendRouting end point missing heading, inferred from geometry";
  }
  command.set_target_speed(FLAGS_sim_bridge_default_target_speed);

  AINFO << "LaneFollowCommand (explicit start):\n" << command.DebugString();
  auto request = std::make_shared<LaneFollowCommand>(command);
  std::shared_ptr<CommandStatus> response;
  for (int attempt = 0; attempt < 8; ++attempt) {
    response = lane_follow_client->SendRequest(request);
    if (response != nullptr) {
      break;
    }
    AWARN << "LaneFollow service not ready, retry " << attempt + 1;
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
  }

  auto is_ok = [](const CommandStatus &resp) {
    const auto st = resp.status();
    const std::string msg = resp.message();
    return st != CommandStatusType::ERROR &&
           msg.find("Cannot get routing") == std::string::npos &&
           msg.find("Failed") == std::string::npos &&
           msg.find("failed") == std::string::npos;
  };

  std::string last_msg;
  bool ok = false;
  if (response == nullptr) {
    last_msg = "LaneFollow service timeout";
  } else {
    last_msg = response->message();
    ok = is_ok(*response);
    if (!ok) {
      AERROR << "LaneFollow routing failed: " << last_msg;
    }
  }

  {
    std::lock_guard<std::mutex> lock(mutex_);
    dest_x_ = raw_ex;
    dest_y_ = raw_ey;
    has_destination_ = ok;
    arrive_hold_frames_ = 0;
    if (ok) {
      ClearPlanning();
    }
  }

  Json data;
  if (response == nullptr) {
    data["ok"] = false;
    data["accepted"] = false;
    data["message"] = last_msg;
    AERROR << "LaneFollowCommand service call failed after retries";
  } else {
    data["ok"] = ok;
    data["accepted"] = ok;
    data["message"] =
        ok ? last_msg
           : (last_msg.empty()
                  ? "Routing failed: end lane not reachable from start"
                  : last_msg);
    data["status"] = static_cast<int>(response->status());
    data["start"] = Json{{"x", sx}, {"y", sy}, {"heading", sh}};
    data["end"] = Json{{"x", raw_ex}, {"y", raw_ey}};
    AINFO << "LaneFollowCommand status=" << data["status"]
          << " ok=" << ok << " msg=" << data["message"];
  }
  return data;
}

void SimBridge::HandleSendRouting(const Json &json, Connection *conn) {
  const Json &points = json["data"]["points"];
  if (!points.is_array() || points.empty()) {
    Json err{{"ok", false}, {"message", "SendRouting needs >= 1 points"}};
    Reply(json, conn, err);
    return;
  }
  double sx = 0.0;
  double sy = 0.0;
  double sh = 0.0;
  double sz = 0.0;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (json["data"].contains("startPoint")) {
      const Json &sp = json["data"]["startPoint"];
      start_x_ = sp.value("x", start_x_);
      start_y_ = sp.value("y", start_y_);
      start_z_ = sp.value("z", start_z_);
      start_heading_ = sp.value("heading", start_heading_);
    }
    // 按前端 scenario JSON 的 startPoint / waypoints 原样发 LaneFollow，不吸附中心线。
    // SnapStartPoseToLaneLocked();
    sx = start_x_;
    sy = start_y_;
    sh = start_heading_;
    sz = start_z_;
  }
  EnsureMapService();
  PublishPoseAt(sx, sy, sz, sh, 8);
  const Json result = ExecuteLaneFollowRouting(points, sx, sy, sh);
  const bool ok = result.value("ok", false) && result.value("accepted", false);
  if (!ok) {
    const std::string msg = result.value("message", "routing failed");
    FailSimulationDueToRouting(msg);
  }
  Reply(json, conn, result);
}

void SimBridge::HandlePreviewRouting(const Json &json, Connection *conn) {
  const Json &data = json["data"];
  const Json &points = data["points"];
  if (!points.is_array() || points.empty()) {
    Json err{{"ok", false}, {"message", "PreviewRouting needs >= 1 points"}};
    Reply(json, conn, err);
    return;
  }
  if (!data.contains("startPoint")) {
    Json err{{"ok", false}, {"message", "PreviewRouting needs startPoint"}};
    Reply(json, conn, err);
    return;
  }
  const Json &sp = data["startPoint"];
  const double sx = sp.value("x", 0.0);
  const double sy = sp.value("y", 0.0);
  const double sz = sp.value("z", 0.0);
  const double sh = sp.value("heading", 0.0);

  std::thread([this, json, conn, points, sx, sy, sz, sh]() {
    EnsureMapService();
    EnsureRoutingModuleForPreview();
    PublishPoseAt(sx, sy, sz, sh, 5);
    const Json result = ExecuteLaneFollowRouting(points, sx, sy, sh);
    Reply(json, conn, result);
  }).detach();
}

void SimBridge::HandleUploadObstacles(const Json &json, Connection *conn) {
  // Legacy path: editor streaming obstacles. Prefer LoadScenario + World agents.
  const Json &obstacles = json["data"]["obstacles"];
  std::lock_guard<std::mutex> lock(mutex_);
  uploaded_obstacles_.clear();
  if (obstacles.is_array()) {
    for (const auto &o : obstacles) {
      UploadedObstacle obstacle;
      obstacle.id = o.value("id", "");
      obstacle.type = o.value("type", "vehicle");
      obstacle.x = o.value("x", 0.0);
      obstacle.y = o.value("y", 0.0);
      obstacle.z = o.value("z", 0.0);
      obstacle.heading = o.value("heading", 0.0);
      obstacle.speed = o.value("speed", 0.0);
      obstacle.length = o.value("length", 0.0);
      obstacle.width = o.value("width", 0.0);
      obstacle.height = o.value("height", 0.0);
      if (o.contains("trajectory") && o["trajectory"].is_array()) {
        for (const auto &tp : o["trajectory"]) {
          GtTrajectoryPoint p;
          p.x = tp.value("x", 0.0);
          p.y = tp.value("y", 0.0);
          p.z = tp.value("z", 0.0);
          p.heading = tp.value("heading", obstacle.heading);
          p.speed = tp.value("speed", obstacle.speed);
          p.relative_time =
              tp.value("relativeTime", tp.value("relative_time", 0.0));
          obstacle.trajectory.push_back(p);
        }
      }
      uploaded_obstacles_.push_back(obstacle);
    }
  }
}

void SimBridge::HandleLoadScenario(const Json &json, Connection *conn) {
  if (world_ == nullptr) {
    Reply(json, conn,
          Json{{"ok", false}, {"message", "World not attached to bridge"}});
    return;
  }
  const Json &data = json["data"];
  Scenario scenario;
  bool ok = false;
  if (data.contains("path") && data["path"].is_string()) {
    ok = ScenarioLoader::LoadFromJsonFile(data["path"].get<std::string>(),
                                          &scenario);
  } else if (data.contains("scenario")) {
    ok = ScenarioLoader::LoadFromJsonString(data["scenario"].dump(), &scenario);
  } else if (data.contains("json") && data["json"].is_string()) {
    ok = ScenarioLoader::LoadFromJsonString(data["json"].get<std::string>(),
                                            &scenario);
  } else {
    Reply(json, conn,
          Json{{"ok", false},
               {"message", "LoadScenario needs data.path or data.scenario"}});
    return;
  }
  if (!ok || !world_->Load(scenario)) {
    Reply(json, conn,
          Json{{"ok", false}, {"message", "failed to load scenario into World"}});
    return;
  }
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (world_->ego()->has_config()) {
      start_x_ = world_->ego()->x();
      start_y_ = world_->ego()->y();
      start_z_ = world_->ego()->z();
      start_heading_ = world_->ego()->heading();
      if (!world_->ego()->vehicle_profile().empty()) {
        current_vehicle_ = world_->ego()->vehicle_profile();
      }
    }
    if (scenario.has_sim_config()) {
      desired_modules_["planning"] = scenario.sim_config().planning();
      desired_modules_["control"] = scenario.sim_config().control();
      desired_modules_["prediction"] = scenario.sim_config().prediction();
      desired_modules_["routing"] = scenario.sim_config().routing();
    }
    if (scenario.has_map_id() && !scenario.map_id().empty()) {
      current_map_ = scenario.map_id();
    }
  }
  BroadcastRuntimeLog("info", "Scenario loaded: " + scenario.name());
  BroadcastHmiStatus();
  Reply(json, conn,
        Json{{"ok", true},
             {"name", scenario.name()},
             {"agents", static_cast<int>(world_->agents().size())},
             {"message", "scenario loaded into WorldSim"}});
}

// ---------------- cyber 回调 ----------------

void SimBridge::ClearPlanning() {
  current_trajectory_.Clear();
  received_planning_ = false;
}

void SimBridge::OnPlanningCommand(
    const std::shared_ptr<PlanningCommand> &planning_command) {
  if (planning_command == nullptr) {
    return;
  }
  BroadcastRoutingPath(*planning_command);
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!sim_running_) {
      return;
    }
    // 与 SimPerfectControl::OnPlanningCommand 一致：只更新 routing header，
    // 不在此处 ReSetPoinstion，否则 planning 就绪后车会被拉回起点。
    AINFO << "PlanningCommand seq=" << planning_command->header().sequence_num()
          << " ts=" << planning_command->header().timestamp_sec();
  }
}

void SimBridge::OnRoutingResponse(
    const std::shared_ptr<RoutingResponse> &routing_response) {
  if (routing_response == nullptr || routing_response->road_size() == 0) {
    return;
  }
  BroadcastRoutingPathFromResponse(*routing_response);
}

void SimBridge::OnPlanning(
    const std::shared_ptr<ADCTrajectory> &trajectory) {
  if (trajectory == nullptr) {
    return;
  }
  std::lock_guard<std::mutex> lock(mutex_);
  if (!sim_running_) {
    return;
  }
  if (trajectory->trajectory_point().empty()) {
    if (!empty_planning_warned_) {
      BroadcastRuntimeLog(
          "warn",
          "planning 轨迹为空（规划失败，请查看 /apollo/data/log/planning.log）");
      empty_planning_warned_ = true;
    }
    return;
  }
  empty_planning_warned_ = false;
  current_trajectory_ = *trajectory;
  received_planning_ = true;
  if (trajectory->estop().is_estop()) {
    BroadcastRuntimeLog("warn",
                        "planning estop: " + trajectory->estop().reason());
  }
}

void SimBridge::OnPrediction(
    const std::shared_ptr<prediction::PredictionObstacles> &obstacles) {
  if (obstacles == nullptr) {
    return;
  }
  std::lock_guard<std::mutex> lock(mutex_);
  if (!sim_running_) {
    return;
  }
  latest_prediction_ = *obstacles;
  received_prediction_ = true;
}

void SimBridge::OnControlCommand(
    const std::shared_ptr<control::ControlCommand> &cmd) {
  if (!sim_running_) {
    return;
  }
  Json data;
  data["steeringPercentage"] = cmd->steering_target();
  data["steeringTarget"] = cmd->steering_target();
  data["throttle"] = cmd->throttle();
  data["brake"] = cmd->brake();
  data["speedMps"] = cmd->speed();
  BroadcastJson(Json{{"type", "ControlCommand"}, {"data", data}});
}

// ---------------- 仿真循环（UI 轮询 + 到站检测）----------------

void SimBridge::RunOnce() {
  bool finish_arrived = false;
  bool broadcast_ego = false;
  double ego_x = 0.0;
  double ego_y = 0.0;
  double ego_z = 0.0;
  double ego_heading = 0.0;
  double ego_speed = 0.0;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!sim_running_) {
      return;
    }
    ++frame_counter_;
    if (world_ != nullptr) {
      world_->set_running(true);
      world_->Tick(kSimIntervalMs / 1000.0);
    }
    localization_reader_->Observe();
    if (localization_reader_->Empty()) {
      return;
    }
    const auto &loc = localization_reader_->GetLatestObserved();
    const auto &pose = loc->pose();
    ego_x = pose.position().x();
    ego_y = pose.position().y();
    ego_z = pose.position().z();
    ego_heading = pose.heading();
    ego_speed = std::hypot(pose.linear_velocity().x(),
                           pose.linear_velocity().y());
    if (world_ != nullptr) {
      world_->UpdateEgoPose(ego_x, ego_y, ego_z, ego_heading, ego_speed);
    }
    finish_arrived = MaybeFinishOnArrivalLocked(ego_x, ego_y, ego_speed);
    broadcast_ego =
        (frame_counter_ % kEgoStateEveryN == 0) || finish_arrived;
  }
  if (finish_arrived) {
    if (world_ != nullptr) {
      world_->set_running(false);
    }
    StopSimControl();
    StopRunningModules();
    BroadcastHmiStatus();
    BroadcastJson(Json{{"type", "SimEvent"},
                       {"data",
                        {{"event", "arrived"},
                         {"message", "已到达终点，仿真结束"}}}});
    return;
  }
  if (broadcast_ego) {
    BroadcastEgoState(ego_x, ego_y, ego_z, ego_heading, ego_speed);
    BroadcastAgentsState();
  }
  MaybeBroadcastTrajectory();
}

bool SimBridge::MaybeFinishOnArrivalLocked(double x, double y, double speed) {
  // caller holds mutex_
  if (!sim_running_ || !has_destination_) {
    return false;
  }
  const double dist = std::hypot(x - dest_x_, y - dest_y_);
  const double abs_speed = std::abs(speed);
  // 到终点附近且基本静止，持续 ~0.5s（100Hz）
  constexpr double kArriveDistM = 2.0;
  constexpr double kArriveSpeedMps = 0.5;
  constexpr int kArriveHoldFrames = 30;  // ~0.3s @ 100Hz
  if (dist <= kArriveDistM && abs_speed <= kArriveSpeedMps) {
    ++arrive_hold_frames_;
  } else if (dist <= kArriveDistM * 0.5) {
    // 已非常接近终点：即便还有轻微蠕动也累计
    ++arrive_hold_frames_;
  } else {
    arrive_hold_frames_ = 0;
  }
  if (arrive_hold_frames_ < kArriveHoldFrames) {
    return false;
  }
  AINFO << "Arrived destination dist=" << dist << " speed=" << abs_speed
        << " — stop sim and modules";
  sim_running_ = false;
  has_destination_ = false;
  arrive_hold_frames_ = 0;
  received_prediction_ = false;
  for (auto &kv : desired_modules_) {
    kv.second = false;
  }
  return true;
}

void SimBridge::PublishGtObstacles() {
  // Prefer World agents (scenario JSON). Fall back to legacy UploadObstacles.
  std::vector<AgentState> world_agents;
  std::vector<UploadedObstacle> obstacles;
  bool use_world = false;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!sim_running_) {
      return;
    }
    if (world_ != nullptr && !world_->agents().empty()) {
      world_agents = world_->SnapshotAgents();
      use_world = true;
    } else {
      obstacles = uploaded_obstacles_;
    }
  }
  auto gt = std::make_shared<prediction::PredictionObstacles>();
  FillHeader("worldsim", gt.get());
  const double now = Clock::NowInSeconds();
  gt->mutable_header()->set_timestamp_sec(now);
  gt->set_start_timestamp(now);
  gt->set_end_timestamp(now + 8.0);
  gt->set_perception_error_code(common::ErrorCode::OK);

  auto add_obstacle = [&](const std::string& id, const std::string& type,
                          double x, double y, double z, double heading,
                          double speed, double length, double width,
                          double height,
                          const std::vector<GtTrajectoryPoint>* traj =
                              nullptr) {
    auto *pred = gt->add_prediction_obstacle();
    auto *perc = pred->mutable_perception_obstacle();
    perc->set_id(StableObstacleId(id));
    perc->mutable_position()->set_x(x);
    perc->mutable_position()->set_y(y);
    perc->mutable_position()->set_z(z);
    perc->set_theta(heading);
    perc->mutable_velocity()->set_x(speed * std::cos(heading));
    perc->mutable_velocity()->set_y(speed * std::sin(heading));
    perc->mutable_velocity()->set_z(0.0);
    perc->set_length(length > 0.1 ? length : 4.5);
    perc->set_width(width > 0.1 ? width : 2.0);
    perc->set_height(height > 0.1 ? height : 1.5);
    perc->set_type(PerceptionTypeOf(type));
    perc->set_timestamp(now);
    perc->set_tracking_time(0.0);
    pred->set_timestamp(now);
    pred->set_predicted_period(8.0);

    // 写入轨迹，供 fake_prediction 直接透传（否则仅靠 CV 回退）
    auto *out_traj = pred->add_trajectory();
    out_traj->set_probability(1.0);
    if (traj != nullptr && !traj->empty()) {
      for (const auto& p : *traj) {
        auto *tp = out_traj->add_trajectory_point();
        tp->mutable_path_point()->set_x(p.x);
        tp->mutable_path_point()->set_y(p.y);
        tp->mutable_path_point()->set_z(p.z);
        tp->mutable_path_point()->set_theta(p.heading);
        tp->set_v(p.speed);
        tp->set_relative_time(p.relative_time);
      }
    } else if (speed > 1e-3) {
      constexpr double kHorizon = 8.0;
      constexpr double kDt = 0.1;
      const double vx = speed * std::cos(heading);
      const double vy = speed * std::sin(heading);
      for (double t = 0.0; t <= kHorizon + 1e-9; t += kDt) {
        auto *tp = out_traj->add_trajectory_point();
        tp->mutable_path_point()->set_x(x + vx * t);
        tp->mutable_path_point()->set_y(y + vy * t);
        tp->mutable_path_point()->set_z(z);
        tp->mutable_path_point()->set_theta(heading);
        tp->set_v(speed);
        tp->set_relative_time(t);
      }
    }
  };

  auto type_name = [](AgentType t) -> std::string {
    switch (t) {
      case AGENT_TYPE_PEDESTRIAN:
        return "pedestrian";
      case AGENT_TYPE_STATIC:
      case AGENT_TYPE_LOADER:
        return "static";
      default:
        return "vehicle";
    }
  };

  if (use_world) {
    for (const auto& a : world_agents) {
      // Disable：未激活，不进 gt_obstacles（无预测轨迹）
      if (!a.enabled) {
        continue;
      }
      const double pub_speed = a.moving ? a.speed : 0.0;
      add_obstacle(a.id, type_name(a.type), a.x, a.y, a.z, a.heading, pub_speed,
                   a.length, a.width, a.height, nullptr);
    }
  } else {
    for (const auto &o : obstacles) {
      add_obstacle(o.id, o.type, o.x, o.y, o.z, o.heading, o.speed, o.length,
                   o.width, o.height, &o.trajectory);
    }
  }
  gt_obstacles_writer_->Write(gt);
}

void SimBridge::MaybeBroadcastTrajectory() {
  if (frame_counter_ % kTrajectoryEveryN != 0) {
    return;
  }
  Json points = Json::array();
  prediction::PredictionObstacles prediction;
  bool has_prediction = false;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!sim_running_) {
      return;
    }
    const auto &traj_pts = current_trajectory_.trajectory_point();
    if (traj_pts.size() >= 2) {
      for (const auto &tp : traj_pts) {
        Json p;
        p["x"] = tp.path_point().x();
        p["y"] = tp.path_point().y();
        p["theta"] = tp.path_point().theta();
        p["v"] = tp.v();
        p["a"] = tp.a();
        p["relativeTime"] = tp.relative_time();
        points.push_back(p);
      }
    }
    if (received_prediction_ && frame_counter_ % kPredictionEveryN == 0) {
      prediction = latest_prediction_;
      has_prediction = true;
    }
  }
  BroadcastJson(Json{{"type", "PlanningTrajectory"},
                     {"data", Json{{"points", points}}}});
  if (has_prediction) {
    BroadcastPredictionObstacles(prediction);
  }
}

void SimBridge::BroadcastPredictionObstacles(
    const prediction::PredictionObstacles &obstacles) {
  Json list = Json::array();
  for (const auto &obs : obstacles.prediction_obstacle()) {
    Json item;
    item["id"] = obs.perception_obstacle().id();
    Json trajs = Json::array();
    for (const auto &traj : obs.trajectory()) {
      Json points = Json::array();
      for (const auto &tp : traj.trajectory_point()) {
        Json p;
        p["x"] = tp.path_point().x();
        p["y"] = tp.path_point().y();
        p["z"] = tp.path_point().z();
        p["theta"] = tp.path_point().theta();
        p["v"] = tp.v();
        p["relativeTime"] = tp.relative_time();
        points.push_back(p);
      }
      if (points.size() >= 2) {
        trajs.push_back(Json{{"points", points}});
      }
    }
    if (!trajs.empty()) {
      item["trajectories"] = trajs;
      list.push_back(item);
    }
  }
  BroadcastJson(Json{{"type", "PredictionObstacles"},
                     {"data", Json{{"obstacles", list}}}});
}


}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
