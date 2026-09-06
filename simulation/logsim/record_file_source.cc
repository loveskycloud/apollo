/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/logsim/record_file_source.h"

#include <algorithm>
#include <limits>

#include "cyber/common/log.h"

namespace apollo {
namespace simulation {

bool RecordFileSource::PassesFilter(const std::string& channel) const {
  if (!blacklist_.empty() && blacklist_.count(channel) > 0) {
    return false;
  }
  if (!whitelist_.empty() && whitelist_.count(channel) == 0) {
    return false;
  }
  return true;
}

bool RecordFileSource::LoadAll(const SourceConfig& cfg) {
  std::vector<std::shared_ptr<cyber::record::RecordReader>> readers;
  for (const auto& path : cfg.paths) {
    auto reader = std::make_shared<cyber::record::RecordReader>(path);
    if (!reader->IsValid()) {
      AERROR << "Invalid record file: " << path;
      return false;
    }
    readers.push_back(reader);
  }
  if (readers.empty()) {
    AERROR << "No record paths provided";
    return false;
  }

  std::set<std::string> channel_filter = whitelist_;
  cyber::record::RecordViewer viewer(readers, cfg.begin_ns, cfg.end_ns,
                                     channel_filter);
  if (!viewer.IsValid()) {
    AERROR << "RecordViewer is invalid";
    return false;
  }

  begin_ns_ = viewer.begin_time();
  end_ns_ = viewer.end_time();
  total_messages_ = 0;

  for (auto it = viewer.begin(); it != viewer.end(); ++it) {
    if (!PassesFilter(it->channel_name)) {
      continue;
    }
    SimEvent ev;
    ev.sim_time_ns = it->time;
    ev.type = SimEventType::FILE_MESSAGE;
    ev.tie_breaker = 10;
    ev.channel = it->channel_name;
    ev.payload = it->content;
    events_.push(ev);
    ++total_messages_;
  }
  return true;
}

bool RecordFileSource::Open(const SourceConfig& cfg) {
  while (!events_.empty()) {
    events_.pop();
  }
  begin_ns_ = cfg.begin_ns;
  end_ns_ = cfg.end_ns;
  total_messages_ = 0;
  whitelist_ = cfg.whitelist;
  blacklist_ = cfg.blacklist;
  return LoadAll(cfg);
}

bool RecordFileSource::HasNext() const { return !events_.empty(); }

bool RecordFileSource::Peek(SimEvent* out) const {
  if (!out || events_.empty()) {
    return false;
  }
  *out = events_.front();
  return true;
}

bool RecordFileSource::Next(SimEvent* out) {
  if (!Peek(out)) {
    return false;
  }
  events_.pop();
  return true;
}

}  // namespace simulation
}  // namespace apollo
