/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "modules/simulation/logsim/record_file_source.h"

#include <cstdlib>
#include <filesystem>
#include "cyber/message/protobuf_factory.h"
#include "cyber/record/record_writer.h"
#include "gtest/gtest.h"

namespace apollo {
namespace simulation {
namespace {

class RecordFixture {
 public:
  RecordFixture() {
    char directory[] = "/tmp/logsim_source_test_XXXXXX";
    const char* created = mkdtemp(directory);
    if (created) {
      directory_ = created;
    }
  }
  ~RecordFixture() {
    if (!directory_.empty()) {
      std::filesystem::remove_all(directory_);
    }
  }
  bool Write() {
    if (directory_.empty()) { return false; }
    cyber::record::RecordWriter writer;
    writer.SetIntervalOfFileSegmentation(0);
    writer.SetSizeOfFileSegmentation(0);
    if (!writer.Open(directory_ + "/input.record")) { return false; }
    cyber::message::ProtobufFactory::Instance()->GetDescriptorString(
        cyber::proto::Header::descriptor(), &descriptor);
    const std::string type = cyber::proto::Header::descriptor()->full_name();
    if (!writer.WriteChannel("/tf_static", type, descriptor) ||
        !writer.WriteChannel("/pose", type, descriptor) ||
        !writer.WriteMessage("/tf_static", std::string("static-config"), 50) ||
        !writer.WriteMessage("/pose", std::string("original-pose"), 100)) {
      return false;
    }
    writer.Close();
    for (const auto& item : std::filesystem::directory_iterator(directory_)) {
      if (item.is_regular_file()) { path = item.path().string(); }
    }
    return !path.empty();
  }
  std::string path;
  std::string descriptor;
 private:
  std::string directory_;
};

TEST(RecordFileSourceTest, OpenFailsWithoutPaths) {
  RecordFileSource source;
  SourceConfig cfg;
  EXPECT_FALSE(source.Open(cfg));
  EXPECT_FALSE(source.HasNext());
}

TEST(RecordFileSourceTest, MappingRetainsFilteredOriginalPayloadAndSchema) {
  RecordFixture fixture;
  ASSERT_TRUE(fixture.Write());
  SourceConfig config;
  config.paths = {fixture.path};
  config.whitelist = {"/tf_static"};
  config.blacklist = {"/pose"};
  config.bag_topic_mappings = {{"/pose", "/bag/pose"}};
  RecordFileSource source;
  ASSERT_TRUE(source.Open(config));
  SimEvent event;
  ASSERT_TRUE(source.Next(&event));
  EXPECT_EQ(event.channel, "/tf_static");
  ASSERT_TRUE(source.Next(&event));
  ASSERT_NE(event.bag_reference, nullptr);
  EXPECT_EQ(event.bag_reference->target_topic, "/bag/pose");
  EXPECT_EQ(event.bag_reference->proto_desc, fixture.descriptor);
  EXPECT_EQ(event.payload, "original-pose");
  EXPECT_EQ(event.sim_time_ns, 100u);
}

TEST(RecordFileSourceTest, StaticBootstrapUsesOnlyConfigurationBeforeWindow) {
  RecordFixture fixture;
  ASSERT_TRUE(fixture.Write());
  SourceConfig config;
  config.paths = {fixture.path};
  config.begin_ns = 100;
  config.whitelist = {"/pose"};
  config.bootstrap_channels = {"/tf_static"};
  RecordFileSource source;
  ASSERT_TRUE(source.Open(config));
  auto bootstrap = source.BootstrapEvents();
  ASSERT_EQ(bootstrap.size(), 1u);
  EXPECT_EQ(bootstrap.front().sim_time_ns, 50u);
  EXPECT_EQ(bootstrap.front().payload, "static-config");
  config.required_channels = {"/tf_static"};
  EXPECT_TRUE(source.Open(config));
  config.required_channels = {"/missing-gnss"};
  EXPECT_FALSE(source.Open(config));
}

TEST(RecordFileSourceTest, RequiredInputsAcceptStaticTfInWindowAndRejectMissingSensor) {
  RecordFixture fixture;
  ASSERT_TRUE(fixture.Write());
  SourceConfig config;
  config.paths = {fixture.path};
  config.bootstrap_channels = {"/tf_static"};
  config.require_bootstrap = false;
  config.required_channels = {"/tf_static", "/pose"};
  RecordFileSource source;
  EXPECT_TRUE(source.Open(config));
  config.required_channels = {"/tf_static", "/pointcloud"};
  EXPECT_FALSE(source.Open(config));
}

}  // namespace
}  // namespace simulation
}  // namespace apollo
