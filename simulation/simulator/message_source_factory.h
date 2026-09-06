/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_MESSAGE_SOURCE_FACTORY_H_
#define SIMULATION_SIMULATOR_MESSAGE_SOURCE_FACTORY_H_

#include <memory>

#include "simulation/logsim/record_file_source.h"
#include "simulation/simulator/i_message_source.h"

namespace apollo {
namespace simulation {

class MessageSourceFactory {
 public:
  static std::unique_ptr<IMessageSource> Create(const SourceConfig& cfg) {
    switch (cfg.type) {
      case SourceType::RECORD_FILE:
        return std::make_unique<RecordFileSource>();
      default:
        return nullptr;
    }
  }
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_MESSAGE_SOURCE_FACTORY_H_
