/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/result_sink.h"

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
  records_.clear();
  writer_ = std::make_unique<cyber::record::RecordWriter>();
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
  writer_->WriteMessage(record.channel, record.content, record.sim_time_ns);
  ++written_count_;
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
