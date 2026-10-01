/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_RESULT_SINK_H_
#define SIMULATION_SIMULATOR_RESULT_SINK_H_

#include <cstdint>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "cyber/common/log.h"
#include "cyber/message/protobuf_factory.h"
#include "cyber/record/record_writer.h"
#include "cyber/time/clock.h"

namespace apollo {
namespace simulation {

struct OutputRecord {
  uint64_t sim_time_ns = 0;
  std::string channel;
  std::string content;
};

class ResultSink {
 public:
  bool Open(const std::string& output_path,
            const std::set<std::string>& record_channels);
  void Write(const OutputRecord& record);

  template <typename MessageT>
  bool WriteTyped(const std::string& channel, const MessageT& message,
                  uint64_t sim_time_ns = 0) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!opened_ || !writer_) {
      healthy_ = false;
      return false;
    }
    if (!record_channels_.empty() &&
        record_channels_.count(channel) == 0) {
      return false;
    }
    if (sim_time_ns == 0) {
      sim_time_ns = cyber::Clock::Now().ToNanosecond();
    }
    // RecordWriter's default proto_desc is empty. Persist the full Cyber
    // descriptor/dependency tree so replay and on-demand decoding are portable.
    if (writer_->GetMessageType(channel).empty()) {
      std::string descriptor;
      cyber::message::ProtobufFactory::Instance()->GetDescriptorString(
          MessageT::descriptor(), &descriptor);
      if (descriptor.empty() || !writer_->WriteChannel(
              channel, MessageT::descriptor()->full_name(), descriptor)) {
        healthy_ = false;
        return false;
      }
    }
    if (!writer_->WriteMessage(channel, message, sim_time_ns)) {
      AERROR << "ResultSink WriteTyped failed, channel=" << channel;
      healthy_ = false;
      return false;
    }
    ++written_count_;
    return true;
  }

  void Flush();
  const std::vector<OutputRecord>& records() const { return records_; }
  const std::string& path() const { return path_; }
  uint64_t written_count() const { return written_count_; }
  bool opened() const { return opened_; }
  bool healthy() const { return healthy_; }

 private:
  std::unique_ptr<cyber::record::RecordWriter> writer_;
  std::vector<OutputRecord> records_;
  std::set<std::string> record_channels_;
  std::string path_;
  uint64_t written_count_ = 0;
  bool opened_ = false;
  bool healthy_ = true;
  std::mutex mutex_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_RESULT_SINK_H_
