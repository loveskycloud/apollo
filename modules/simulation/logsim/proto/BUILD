## Auto generated for simulation logsim protos
load("//tools/proto:proto.bzl", "proto_library")
load("//tools:apollo_package.bzl", "apollo_package")

package(default_visibility = ["//visibility:public"])

proto_library(
    name = "channel_policy_proto",
    srcs = ["channel_policy.proto"],
)

proto_library(
    name = "simulation_task_proto",
    srcs = ["simulation_task.proto"],
    deps = [
        ":channel_policy_proto",
    ],
)

apollo_package()
