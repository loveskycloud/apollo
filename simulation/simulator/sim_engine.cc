/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/sim_engine.h"

#include "cyber/common/log.h"

namespace apollo {
namespace simulation {

bool SimEngine::Init(const Options& opts) {
  controller_ = opts.controller;
  fabricated_ = opts.fabricated;
  timer_scheduler_ = opts.timer_scheduler;
  result_sink_ = opts.result_sink;
  progress_ = opts.progress;
  monitor_ = opts.monitor;
  begin_ns_ = opts.begin_ns;
  end_ns_ = opts.end_ns;
  return controller_ != nullptr;
}

int SimEngine::RunAFAP() {
  if (!controller_) {
    return 1;
  }
  AINFO << "SimEngine::RunAFAP begin, window=[" << begin_ns_ << ", " << end_ns_
        << "]";
  if (timer_scheduler_ && fabricated_) {
    timer_scheduler_->RegisterFromSimTimerRegistry(begin_ns_, end_ns_,
                                                   fabricated_);
    controller_->MergeFabricated(fabricated_);
  }
  uint64_t published = 0;
  while (controller_->WaitAndPublishNext(std::chrono::milliseconds(5000))) {
    ++published;
    if (progress_) {
      progress_->OnEvent(begin_ns_, "publish");
    }
    if (monitor_) {
      monitor_->IncInjected();
    }
    if ((published % 1000) == 0) {
      AINFO << "SimEngine published=" << published;
    }
  }
  AINFO << "SimEngine::RunAFAP done, published=" << published;
  if (monitor_ && monitor_->HasFatal()) {
    return 1;
  }
  if (result_sink_) {
    result_sink_->Flush();
  }
  return 0;
}

}  // namespace simulation
}  // namespace apollo
