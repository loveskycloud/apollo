/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_LOGSIM_RECORD_FILE_SOURCE_H_
#define SIMULATION_LOGSIM_RECORD_FILE_SOURCE_H_

#include <limits>
#include <memory>
#include <queue>
#include <set>
#include <string>
#include <vector>

#include "cyber/record/record_reader.h"
#include "cyber/record/record_viewer.h"
#include "simulation/simulator/i_message_source.h"

namespace apollo {
namespace simulation {

class RecordFileSource : public IMessageSource {
 public:
  RecordFileSource() = default;
  ~RecordFileSource() override = default;

  bool Open(const SourceConfig& cfg) override;
  bool HasNext() const override;
  bool Peek(SimEvent* out) const override;
  bool Next(SimEvent* out) override;
  uint64_t begin_ns() const override { return begin_ns_; }
  uint64_t end_ns() const override { return end_ns_; }
  uint64_t total_messages() const override { return total_messages_; }

 private:
  bool LoadAll(const SourceConfig& cfg);
  bool PassesFilter(const std::string& channel) const;

  std::queue<SimEvent> events_;
  uint64_t begin_ns_ = 0;
  uint64_t end_ns_ = std::numeric_limits<uint64_t>::max();
  uint64_t total_messages_ = 0;
  std::set<std::string> whitelist_;
  std::set<std::string> blacklist_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_LOGSIM_RECORD_FILE_SOURCE_H_
