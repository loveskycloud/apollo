load("//tools:apollo_package.bzl", "apollo_cc_binary")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "web_monitor_runtime",
    srcs = [
        "ad_scene.py",
        "apollo_record_to_semantic_mcap.py",
        "hd_map.py",
        "mcap_debug_query.py",
        "mcap_playback_plan.py",
        "mcap_topic_debug.py",
        "python_runtime.py",
        "vehicle_geometry.py",
        "//modules/common_msgs/chassis_msgs:chassis_proto_py_pb2",
        "//modules/common_msgs/prediction_msgs:prediction_obstacle_proto_py_pb2",
        "//modules/common_msgs/sensor_msgs:pointcloud_proto_py_pb2",
        "//modules/common_msgs/transform_msgs:transform_proto_py_pb2",
    ],
)

# The task service decodes the records produced by the packaged simulator.
# Build against the same Cyber ABI instead of shipping the locally installed tool.
apollo_cc_binary(
    name = "apollo_record_tool",
    srcs = ["apollo_record_tool.cc"],
    deps = ["//cyber/record:cyber_record"],
)
