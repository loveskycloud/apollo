/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/result_sink.h"

#include <memory>

#include "cyber/common/log.h"

namespace apollo {
namespace simulation {

bool ResultSink::Open(const std::string& output_path,
                      const std::set<std::string>& record_channels) {
  std::lock_guard<std::mutex> lock(mutex_);
  record_channels_ = record_channels;
  path_ = output_path;
  written_count_ = 0;
  channel_counts_.clear();
  healthy_ = true;
  records_.clear();
  writer_ = std::make_unique<cyber::record::RecordWriter>();
  // Do not split simulation output by time/size (Cyber default is 60s / 2GB).
  writer_->SetIntervalOfFileSegmentation(0);
  writer_->SetSizeOfFileSegmentation(0);
  if (!writer_->Open(output_path)) {
    AERROR << "Failed to open output record: " << output_path;
    opened_ = false;
    return false;
  }
  opened_ = true;
  AINFO << "ResultSink opened: " << output_path
        << " channels=" << record_channels_.size();
  return true;
}

void ResultSink::Write(const OutputRecord& record) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!opened_ || !writer_) {
    return;
  }
  if (!record_channels_.empty() &&
      record_channels_.count(record.channel) == 0) {
    return;
  }
  records_.push_back(record);
  if (!writer_->WriteMessage(record.channel, record.content, record.sim_time_ns)) {
    healthy_ = false;
    return;
  }
  ++written_count_;
  ++channel_counts_[record.channel];
}

bool ResultSink::WriteRaw(const std::string& channel, const std::string& payload,
                          uint64_t sim_time_ns, const std::string& message_type,
                          const std::string& proto_desc) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!opened_ || !writer_ || message_type.empty() || proto_desc.empty() ||
      (!record_channels_.empty() && record_channels_.count(channel) == 0)) {
    healthy_ = false;
    return false;
  }
  if (writer_->GetMessageType(channel).empty()) {
    if (!writer_->WriteChannel(channel, message_type, proto_desc)) {
      healthy_ = false;
      return false;
    }
  } else if (writer_->GetMessageType(channel) != message_type ||
             writer_->GetProtoDesc(channel) != proto_desc) {
    healthy_ = false;
    return false;
  }
  if (!writer_->WriteMessage(channel, payload, sim_time_ns)) {
    healthy_ = false;
    return false;
  }
  ++written_count_;
  ++channel_counts_[channel];
  return true;
}

void ResultSink::Flush() {
  std::lock_guard<std::mutex> lock(mutex_);
  if (writer_) {
    writer_->Close();
    writer_.reset();
  }
  opened_ = false;
  AINFO << "ResultSink flushed: path=" << path_
        << " written=" << written_count_;
}

}  // namespace simulation
}  // namespace apollo
