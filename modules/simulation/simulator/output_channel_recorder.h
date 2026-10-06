/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_OUTPUT_CHANNEL_RECORDER_H_
#define SIMULATION_SIMULATOR_OUTPUT_CHANNEL_RECORDER_H_

#include <memory>
#include <map>
#include <string>
#include <vector>

#include "cyber/node/node.h"
#include "cyber/node/reader_base.h"
#include "modules/simulation/simulator/result_sink.h"

namespace apollo {
namespace simulation {

/**
 * @brief Subscribe Intra channels and dump into ResultSink.
 *
 * MODE_SIMULATION module writers publish typed Intra messages; this recorder
 * creates matching typed IntraReaders so BlockerManager types agree.
 */
class OutputChannelRecorder {
 public:
  bool Start(const std::shared_ptr<cyber::Node>& node, ResultSink* sink,
             const std::vector<std::string>& channels,
             const std::map<std::string, std::string>& channel_types = {});
  void Stop();
  size_t reader_count() const { return readers_.size(); }

 private:
  template <typename MessageT>
  bool AddTypedReader(const std::string& channel);

  std::shared_ptr<cyber::Node> node_;
  ResultSink* sink_ = nullptr;
  std::vector<std::shared_ptr<cyber::ReaderBase>> readers_;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_OUTPUT_CHANNEL_RECORDER_H_
