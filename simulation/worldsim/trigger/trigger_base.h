/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/
#pragma once

#include <memory>
#include <string>
#include <vector>

#include "simulation/worldsim/proto/scenario.pb.h"

namespace apollo {
namespace simulation {
namespace worldsim {

class World;

class TriggerBase {
 public:
  explicit TriggerBase(const TriggerConfig& config);
  virtual ~TriggerBase() = default;

  virtual void Init(World* world);
  /** @return true if the trigger fires this tick (edge-triggered once). */
  virtual bool Evaluate(double sim_time) = 0;
  void Fire();
  bool fired() const { return fired_; }
  const std::string& id() const { return id_; }
  const TriggerConfig& config() const { return config_; }

 protected:
  std::string id_;
  TriggerConfig config_;
  World* world_ = nullptr;
  bool fired_ = false;
};

class TimeTrigger final : public TriggerBase {
 public:
  using TriggerBase::TriggerBase;
  bool Evaluate(double sim_time) override;
};

class LocationTrigger final : public TriggerBase {
 public:
  using TriggerBase::TriggerBase;
  bool Evaluate(double sim_time) override;
};

class AgentDistanceTrigger final : public TriggerBase {
 public:
  using TriggerBase::TriggerBase;
  bool Evaluate(double sim_time) override;
};

class SpeedTrigger final : public TriggerBase {
 public:
  using TriggerBase::TriggerBase;
  bool Evaluate(double sim_time) override;
};

class BehaviorTrigger final : public TriggerBase {
 public:
  using TriggerBase::TriggerBase;
  bool Evaluate(double sim_time) override;
};

std::unique_ptr<TriggerBase> CreateTrigger(const TriggerConfig& config);

}  // namespace worldsim
}  // namespace simulation
}  // namespace apollo
