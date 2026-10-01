load("//tools:apollo_package.bzl", "apollo_component", "apollo_cc_binary", "apollo_cc_library", "apollo_cc_test", "apollo_package")
load("//tools:cpplint.bzl", "cpplint")
load("//tools/install:install.bzl", "install_src_files")

package(default_visibility = ["//visibility:public"])

apollo_cc_library(
    name = "planner",
    hdrs = ["planner.h", "reference_line.h", "model_config.h", "parking_planner.h", "parking_motion.h"],
)

apollo_component(
    name = "libml_planning.so",
    srcs = ["component.cc"],
    deps = [
        ":planner",
        "//modules/simulation/ml_planning/proto:ml_planning_config_proto",
        "//cyber",
        "//modules/common/configs:vehicle_config_helper",
        "//modules/common/math:math",
        "//modules/common_msgs/localization_msgs:localization_proto",
        "//modules/common_msgs/perception_msgs:perception_obstacle_proto",
        "//modules/common_msgs/planning_msgs:planning_proto",
        "//modules/common_msgs/planning_msgs:planning_command_proto",
        "//modules/common_msgs/planning_msgs:pad_msg_proto",
        "//modules/common_msgs/external_command_msgs:command_status_proto",
        # Temporarily disabled until external-command-proto is available.
        # "//modules/external_command/proto:temporary_stop_command_proto",
        # "//modules/external_command/proto:reference_line_offset_command_proto",
        "//modules/common_msgs/routing_msgs:routing_proto",
        "//modules/map:apollo_map",
    ],
)

apollo_cc_test(
    name = "planner_test",
    size = "small",
    srcs = ["planner_test.cc"],
    deps = [":planner", "@com_google_googletest//:gtest_main"],
    linkstatic = True,
)

filegroup(
    name = "runtime_data",
    srcs = glob(["dag/**", "conf/**", "models/v*/unified.weights"]),
)

# This research package contains local Python environments and experiment bags.
# Do not recursively install them into the Apollo SDK.
# Proto compilation and installation resolve through @apollo_src, so publish
# the proto subpackage even though this package skips its other source trees.
install_src_files(
    name = "install_src",
    deps = ["//modules/simulation/ml_planning/proto:install_src"],
    type = "neo",
)


apollo_cc_binary(
    name = "command_fixture_writer",
    srcs = ["command_fixture_writer.cc"],
    deps = ["//cyber/record:cyber_record"],
)

apollo_package()
cpplint()
