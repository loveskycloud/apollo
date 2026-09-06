/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_I_MESSAGE_SOURCE_H_
#define SIMULATION_SIMULATOR_I_MESSAGE_SOURCE_H_

#include <cstdint>
#include <limits>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "simulation/simulator/sim_event.h"

namespace apollo {
namespace simulation {

enum class SourceType { RECORD_FILE };

struct SourceConfig {
  SourceType type = SourceType::RECORD_FILE;
  std::vector<std::string> paths;
  uint64_t begin_ns = 0;
  uint64_t end_ns = std::numeric_limits<uint64_t>::max();
  std::set<std::string> whitelist;
  std::set<std::string> blacklist;
};

class IMessageSource {
 public:
  virtual ~IMessageSource() = default;
  virtual bool Open(const SourceConfig& cfg) = 0;
  virtual bool HasNext() const = 0;
  virtual bool Peek(SimEvent* out) const = 0;
  virtual bool Next(SimEvent* out) = 0;
  virtual uint64_t begin_ns() const = 0;
  virtual uint64_t end_ns() const = 0;
  virtual uint64_t total_messages() const = 0;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_I_MESSAGE_SOURCE_H_
