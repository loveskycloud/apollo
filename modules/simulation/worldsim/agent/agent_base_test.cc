#include "modules/simulation/worldsim/agent/agent_base.h"
#include "modules/simulation/worldsim/core/collision.h"

#include <cmath>
#include <limits>
#include "gtest/gtest.h"

namespace apollo::simulation::worldsim {
namespace {
class TestAgent : public AgentBase {
 public:
  using AgentBase::AgentBase;
  void Tick(double) override {}
};

TEST(AgentGeometry, PreservesThinAndSmallObjects) {
  for (double size : {0.001, 0.01876308210194111, 0.1, 0.101}) {
    AgentConfig config;
    config.mutable_size()->set_x(size);
    config.mutable_size()->set_y(size);
    config.mutable_size()->set_z(size);
    const TestAgent agent(config);
    EXPECT_DOUBLE_EQ(agent.state().length, size);
    EXPECT_DOUBLE_EQ(agent.state().width, size);
    EXPECT_DOUBLE_EQ(agent.state().height, size);
  }
}

TEST(AgentGeometry, RetainsDefaultsForMissingOrInvalidDimensions) {
  const TestAgent missing{AgentConfig{}};
  EXPECT_DOUBLE_EQ(missing.state().length, 4.5);
  EXPECT_DOUBLE_EQ(missing.state().width, 2.0);
  EXPECT_DOUBLE_EQ(missing.state().height, 1.5);
  for (double value : {0., -1., std::numeric_limits<double>::infinity(),
                       std::numeric_limits<double>::quiet_NaN()}) {
    AgentConfig config;
    config.mutable_size()->set_x(value);
    config.mutable_size()->set_y(value);
    config.mutable_size()->set_z(value);
    const TestAgent agent(config);
    EXPECT_DOUBLE_EQ(agent.state().length, 4.5);
    EXPECT_DOUBLE_EQ(agent.state().width, 2.0);
    EXPECT_DOUBLE_EQ(agent.state().height, 1.5);
  }
}

TEST(AgentGeometry, CapturedSmallObjectDoesNotBecomeFalseCollision) {
  const double heading = 1.3257141838121864;
  const BodyBox ego{9994.211402885936 + .26 * std::cos(heading),
                    9000002.137795933 + .26 * std::sin(heading),
                    heading, .36, .25};
  AgentConfig config;
  config.mutable_position()->set_x(9993.512192163073);
  config.mutable_position()->set_y(9000002.28961995);
  config.set_heading(1.3417527675628662);
  config.mutable_size()->set_x(0.03536112233996391);
  config.mutable_size()->set_y(0.01876308210194111);
  config.mutable_size()->set_z(0.039284784346818924);
  const TestAgent agent(config);
  const auto& s = agent.state();
  EXPECT_FALSE(BodiesCollide(ego, {s.x, s.y, s.heading, s.length/2, s.width/2}));
  // Small objects must still count when they actually intersect the car.
  EXPECT_TRUE(BodiesCollide(ego, {ego.x, ego.y, s.heading, s.length/2, s.width/2}));
}
}  // namespace
}  // namespace apollo::simulation::worldsim
