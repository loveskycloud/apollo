/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/sim_initializer.h"

#include <chrono>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <set>
#include <sstream>
#include <utility>
#include <vector>

#include "cyber/common/file.h"
#include "cyber/common/global_data.h"
#include "cyber/common/log.h"
#include "cyber/cyber.h"
#include "cyber/init.h"
#include "cyber/time/clock.h"
#include "google/protobuf/text_format.h"

#include "simulation/logsim/record_file_source.h"
#include "simulation/simulator/message_source_factory.h"

namespace apollo {
namespace simulation {
namespace {

std::string FormatTimestampForPath() {
  const auto now = std::chrono::system_clock::now();
  const std::time_t t = std::chrono::system_clock::to_time_t(now);
  std::tm tm_buf{};
  localtime_r(&t, &tm_buf);
  std::ostringstream oss;
  oss << std::put_time(&tm_buf, "%Y%m%d_%H%M%S");
  return oss.str();
}

// Default: data/simulation/<scenario_id>/sim_run_<timestamp>.record
std::string ResolveOutputRecordPath(const logsim::SimulationTask& task) {
  const char* env_path = std::getenv("SIM_OUTPUT_RECORD");
  if (env_path != nullptr && env_path[0] != '\0') {
    return std::string(env_path);
  }
  if (!task.output_record_path().empty()) {
    return task.output_record_path();
  }
  std::string dir = task.output_record_dir().empty() ? "data/simulation"
                                                    : task.output_record_dir();
  if (!task.scenario_id().empty()) {
    dir += "/" + task.scenario_id();
  }
  return dir + "/sim_run_" + FormatTimestampForPath() + ".record";
}

}  // namespace

bool SimInitializer::LoadTask(const std::string& task_dir,
                              logsim::SimulationTask* task) {
  const std::string path = task_dir + "/task.pb.txt";
  std::string content;
  if (!cyber::common::GetContent(path, &content)) {
    return false;
  }
  return google::protobuf::TextFormat::ParseFromString(content, task);
}

bool SimInitializer::SetupCyber(const logsim::SimulationTask& task) {
  // Cyber loads: $CYBER_PATH/conf/cyber.pb.conf
  // Install cyber_sim.pb.conf into a dedicated work root under task_dir.
  std::string conf_src = "simulation/simulator/conf/cyber_sim.pb.conf";
  if (!task.cyber_conf_path().empty()) {
    // Accept either a file path or a directory containing cyber_sim.pb.conf /
    // cyber.pb.conf.
    if (cyber::common::PathExists(task.cyber_conf_path()) &&
        !cyber::common::DirectoryExists(task.cyber_conf_path())) {
      conf_src = task.cyber_conf_path();
    } else {
      const std::string cand1 =
          task.cyber_conf_path() + "/cyber_sim.pb.conf";
      const std::string cand2 = task.cyber_conf_path() + "/cyber.pb.conf";
      if (cyber::common::PathExists(cand1)) {
        conf_src = cand1;
      } else if (cyber::common::PathExists(cand2)) {
        conf_src = cand2;
      }
    }
  }
  if (!cyber::common::PathExists(conf_src)) {
    AERROR << "cyber conf source not found: " << conf_src;
    return false;
  }

  const std::string work_root = task.task_dir() + "/cyber_runtime";
  const std::string conf_dir = work_root + "/conf";
  const std::string conf_dst = conf_dir + "/cyber.pb.conf";
  if (!cyber::common::EnsureDirectory(conf_dir)) {
    AERROR << "failed to create cyber runtime conf dir: " << conf_dir;
    return false;
  }
  if (!cyber::common::CopyFile(conf_src, conf_dst)) {
    AERROR << "failed to install cyber conf: " << conf_src << " -> "
           << conf_dst;
    return false;
  }

  // Prefer absolute CYBER_PATH so InitConfig is cwd-independent.
  std::string abs_work_root = work_root;
  if (!cyber::common::PathIsAbsolute(work_root)) {
    abs_work_root = cyber::common::GetCurrentPath() + "/" + work_root;
  }
  setenv("CYBER_PATH", abs_work_root.c_str(), 1);
  AINFO << "SetupCyber: CYBER_PATH=" << abs_work_root
        << " conf=" << conf_dst;

  if (!cyber::Init("simulator_main")) {
    AERROR << "cyber::Init failed";
    return false;
  }
  cyber::common::GlobalData::Instance()->EnableSimulationMode();
  cyber::Clock::SetMode(cyber::proto::MODE_MOCK);
  AINFO << "SetupCyber: cyber::Init done, simulation mode enabled";
  return true;
}

bool SimInitializer::Warmup(const logsim::SimulationTask& task) {
  if (task.map_dir().empty() || task.vehicle_config_path().empty()) {
    return false;
  }
  return true;
}

bool SimInitializer::Init(const std::string& task_dir, Context* ctx) {
  if (!ctx || !LoadTask(task_dir, &ctx->task)) {
    AERROR << "LoadTask failed: " << task_dir;
    return false;
  }
  ctx->task.set_task_dir(task_dir);
  if (!SetupCyber(ctx->task)) {
    AERROR << "SetupCyber failed";
    return false;
  }
  if (!Warmup(ctx->task)) {
    ctx->monitor.AddFatal("warmup failed: map or vehicle not configured");
    AERROR << "Warmup failed";
    return false;
  }

  auto node_up = cyber::CreateNode("simulator_main");
  if (!node_up) {
    AERROR << "CreateNode failed";
    return false;
  }
  ctx->node = std::shared_ptr<cyber::Node>(std::move(node_up));
  AINFO << "CreateNode ok";

  std::vector<std::string> inject_channels;
  for (const auto& c : ctx->task.channel_policy().inject_channels()) {
    inject_channels.push_back(c);
  }
  if (!ctx->consumer.Init(ctx->node, inject_channels)) {
    AERROR << "MessageConsumer::Init failed";
    return false;
  }
  AINFO << "MessageConsumer init ok, inject=" << inject_channels.size();

  SourceConfig src_cfg;
  src_cfg.type = SourceType::RECORD_FILE;
  for (const auto& p : ctx->task.record_paths()) {
    src_cfg.paths.push_back(p);
  }
  if (ctx->task.log_start_s() > 0) {
    src_cfg.begin_ns = static_cast<uint64_t>(ctx->task.log_start_s() * 1e9);
  }
  if (ctx->task.log_end_s() > 0) {
    src_cfg.end_ns = static_cast<uint64_t>(ctx->task.log_end_s() * 1e9);
  }
  for (const auto& channel : inject_channels) {
    src_cfg.whitelist.insert(channel);
  }
  if (ctx->task.input_kind() == logsim::SimulationTask::WORLD) {
    src_cfg.type = SourceType::WORLD_SCENARIO;
    src_cfg.paths = {ctx->task.world_scenario_path()};
    src_cfg.begin_ns = ctx->task.world_start_ns();
    src_cfg.step_ms = ctx->task.step_ms();
    src_cfg.ego_model = ctx->task.ego_model();
    src_cfg.consumer = &ctx->consumer;
  }
  AINFO << "opening record source, paths=" << src_cfg.paths.size();
  auto source = MessageSourceFactory::Create(src_cfg);
  if (!source || !source->Open(src_cfg)) {
    ctx->monitor.AddFatal("failed to open record source");
    AERROR << "RecordFileSource::Open failed";
    return false;
  }
  AINFO << "record opened, begin_ns=" << source->begin_ns()
        << " end_ns=" << source->end_ns()
        << " total=" << source->total_messages();
  const uint64_t record_begin_ns = source->begin_ns();
  const uint64_t record_end_ns = source->end_ns();
  cyber::Clock::SetNow(cyber::Time(record_begin_ns));

  EmulatorController::Options ec_opts;
  ec_opts.source = std::move(source);
  ec_opts.consumer = &ctx->consumer;
  ec_opts.channel_policy = ctx->task.channel_policy();
  if (!ctx->controller.Init(ec_opts)) {
    ctx->monitor.AddFatal("emulator controller init failed");
    AERROR << "EmulatorController::Init failed";
    return false;
  }
  AINFO << "EmulatorController::Init ok, loading messages...";
  if (!ctx->controller.LoadFromSource()) {
    ctx->monitor.AddFatal("emulator controller load failed");
    AERROR << "EmulatorController::LoadFromSource failed";
    return false;
  }
  AINFO << "EmulatorController::LoadFromSource ok";

  std::set<std::string> record_channels;
  for (const auto& c : ctx->task.channel_policy().record_channels()) {
    record_channels.insert(c);
  }
  // Also keep inject channels in the sim bag so the record is self-contained.
  for (const auto& c : ctx->task.channel_policy().inject_channels()) {
    record_channels.insert(c);
  }
  const std::string output_path = ResolveOutputRecordPath(ctx->task);
  const std::string output_dir = cyber::common::GetDirName(output_path);
  AINFO << "opening ResultSink: " << output_path;
  if (!output_dir.empty() && !cyber::common::EnsureDirectory(output_dir)) {
    AERROR << "failed to create output dir: " << output_dir;
    return false;
  }
  if (!ctx->result_sink.Open(output_path, record_channels)) {
    AERROR << "ResultSink::Open failed: " << output_path;
    return false;
  }
  AINFO << "ResultSink open ok: " << output_path;

  std::vector<std::string> recorder_channels(record_channels.begin(),
                                             record_channels.end());
  if (!ctx->output_recorder.Start(ctx->node, &ctx->result_sink,
                                  recorder_channels)) {
    AERROR << "OutputChannelRecorder failed; refusing incomplete simulation bag";
    return false;
  }

  SimProgressState ps;
  ps.scenario_id = ctx->task.scenario_id();
  ps.begin_s = static_cast<double>(record_begin_ns) / 1e9;
  ps.end_s = static_cast<double>(record_end_ns) / 1e9;
  ctx->progress.Init(ps);

  SimEngine::Options eng_opts;
  eng_opts.controller = &ctx->controller;
  eng_opts.fabricated = &ctx->fabricated;
  eng_opts.timer_scheduler = &ctx->timer_scheduler;
  eng_opts.result_sink = &ctx->result_sink;
  eng_opts.progress = &ctx->progress;
  eng_opts.monitor = &ctx->monitor;
  eng_opts.begin_ns = record_begin_ns;
  eng_opts.end_ns = record_end_ns;
  eng_opts.progress_path = ctx->task.progress_path();
  if (!ctx->engine.Init(eng_opts)) {
    AERROR << "SimEngine::Init failed";
    return false;
  }
  ctx->monitor.SetModuleInitOk(true);
  AINFO << "SimInitializer::Init complete, window=[" << record_begin_ns
        << ", " << record_end_ns << "]";
  return true;
}

int SimInitializer::Run(Context* ctx) {
  if (!ctx) {
    return 1;
  }
  return ctx->engine.RunAFAP();
}

}  // namespace simulation
}  // namespace apollo
