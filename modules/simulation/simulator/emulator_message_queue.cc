/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/simulator/emulator_message_queue.h"

#include <algorithm>
#include <utility>

namespace apollo {
namespace simulation {

void EmulatorMessageQueue::Push(SimEvent ev) {
  std::lock_guard<std::mutex> lock(mutex_);
  queue_.push(std::move(ev));
}

bool EmulatorMessageQueue::Empty() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return queue_.empty();
}

bool EmulatorMessageQueue::Peek(SimEvent* out) const {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!out || queue_.empty()) {
    return false;
  }
  *out = queue_.top();
  return true;
}

bool EmulatorMessageQueue::Pop(SimEvent* out) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!out || queue_.empty()) {
    return false;
  }
  *out = queue_.top();
  queue_.pop();
  return true;
}

size_t EmulatorMessageQueue::Size() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return queue_.size();
}

void FabricatedMessageQueue::Push(SimEvent ev) {
  std::lock_guard<std::mutex> lock(mutex_);
  queue_.push(std::move(ev));
}

bool FabricatedMessageQueue::Empty() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return queue_.empty();
}

std::vector<SimEvent> FabricatedMessageQueue::DrainAll() {
  std::lock_guard<std::mutex> lock(mutex_);
  std::vector<SimEvent> result;
  while (!queue_.empty()) {
    result.push_back(queue_.top());
    queue_.pop();
  }
  std::sort(result.begin(), result.end());
  return result;
}

}  // namespace simulation
}  // namespace apollo
