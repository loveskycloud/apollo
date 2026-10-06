/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/logsim/record_file_source.h"

#include <algorithm>
#include <limits>
#include <map>

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
  std::map<std::string, std::shared_ptr<BagReferenceChannel>> references;
  for (const auto& path : cfg.paths) {
    auto reader = std::make_shared<cyber::record::RecordReader>(path);
    if (!reader->IsValid()) {
      AERROR << "Invalid record file: " << path;
      return false;
    }
    readers.push_back(reader);
    for (const auto& channel : reader->GetChannelList()) {
      const auto type = reader->GetMessageType(channel);
      const auto previous = channel_types_.find(channel);
      if (previous != channel_types_.end() && previous->second != type) {
        AERROR << "Conflicting record channel type: " << channel;
        return false;
      }
      channel_types_[channel] = type;
    }
    for (const auto& mapping : cfg.bag_topic_mappings) {
      if (reader->GetChannelList().count(mapping.first) == 0) {
        continue;
      }
      auto schema = std::make_shared<BagReferenceChannel>();
      schema->source_topic = mapping.first;
      schema->target_topic = mapping.second;
      schema->message_type = reader->GetMessageType(mapping.first);
      schema->proto_desc = reader->GetProtoDesc(mapping.first);
      const auto previous = references.find(mapping.first);
      if (schema->message_type.empty() || schema->proto_desc.empty() ||
          (previous != references.end() &&
           (previous->second->message_type != schema->message_type ||
            previous->second->proto_desc != schema->proto_desc))) {
        AERROR << "Missing or conflicting bag schema: " << mapping.first;
        return false;
      }
      references[mapping.first] = std::move(schema);
    }
  }
  if (readers.empty()) {
    AERROR << "No record paths provided";
    return false;
  }

  std::set<std::string> channel_filter = whitelist_;
  if (!channel_filter.empty()) {
    for (const auto& mapping : cfg.bag_topic_mappings) {
      channel_filter.insert(mapping.first);
    }
  }
  cyber::record::RecordViewer viewer(readers, cfg.begin_ns, cfg.end_ns,
                                     channel_filter);
  if (!viewer.IsValid()) {
    AERROR << "RecordViewer is invalid";
    return false;
  }

  begin_ns_ = viewer.begin_time();
  end_ns_ = viewer.end_time();
  total_messages_ = 0;
  std::set<std::string> seen_channels;

  for (auto it = viewer.begin(); it != viewer.end(); ++it) {
    const auto reference = references.find(it->channel_name);
    if (!PassesFilter(it->channel_name) && reference == references.end()) {
      continue;
    }
    SimEvent ev;
    ev.sim_time_ns = it->time;
    ev.type = SimEventType::FILE_MESSAGE;
    ev.tie_breaker = 10;
    ev.channel = it->channel_name;
    if (reference != references.end()) {
      ev.bag_reference = reference->second;
    }
    // Apollo's periodically latched command carries the same PlanningCommand
    // schema. Re-inject into the live command input, retaining record time.
    if (ev.channel == "/apollo/planning_command_history") {
      ev.channel = "/apollo/planning/command";
    }
    ev.payload = it->content;
    ev.sequence = total_messages_;
    seen_channels.insert(it->channel_name);
    events_.push(ev);
    ++total_messages_;
  }
  if (total_messages_ == 0) {
    AERROR << "No messages in requested record window / channel selection";
    return false;
  }
  if (!cfg.bootstrap_channels.empty()) {
    cyber::record::RecordViewer bootstrap(readers, 0, begin_ns_,
                                          cfg.bootstrap_channels);
    for (auto it = bootstrap.begin(); it != bootstrap.end(); ++it) {
      SimEvent event;
      event.sim_time_ns = it->time;
      event.channel = it->channel_name;
      event.payload = it->content;
      seen_channels.insert(event.channel);
      bootstrap_events_.push_back(std::move(event));
    }
    if (bootstrap_events_.empty() && cfg.require_bootstrap) {
      AERROR << "Localization requires /tf_static at or before the replay start; "
             << "include the record segment containing static extrinsics";
      return false;
    }
  }
  for (const auto& channel : cfg.required_channels) {
    if (seen_channels.count(channel) == 0) {
      AERROR << "Required sensor input missing in replay window or bootstrap: " << channel;
      return false;
    }
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
  bootstrap_events_.clear();
  channel_types_.clear();
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
