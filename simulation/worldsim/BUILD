load("//tools:apollo_package.bzl", "apollo_cc_binary", "apollo_cc_library", "apollo_package")
load("//tools:cpplint.bzl", "cpplint")

package(default_visibility = ["//visibility:public"])

exports_files(["world_message_source.cc", "world_message_source.h"])

WORLDSIM_COPTS = ['-DMODULE_NAME=\\"worldsim\\"']

apollo_cc_library(
    name = "worldsim_core",
    srcs = [
        "agent/agent_base.cc",
        "agent/agents.cc",
        "agent/bezier_path.cc",
        "core/scenario_loader.cc",
        "core/world.cc",
        "ego/ego_car.cc",
        "trigger/trigger_base.cc",
    ],
    hdrs = [
        "agent/agent_base.h",
        "agent/agents.h",
        "agent/bezier_path.h",
        "core/scenario_loader.h",
        "core/world.h",
        "ego/ego_car.h",
        "trigger/trigger_base.h",
    ],
    copts = WORLDSIM_COPTS,
    deps = [
        "//cyber",
        "//modules/simulation/worldsim/proto:scenario_proto",
        "@com_google_protobuf//:protobuf",
    ],
)

apollo_cc_binary(
    name = "worldsim",
    srcs = ["main.cc"],
    deps = ["//modules/simulation/simulator:apollo_simulation_core"],
)

filegroup(
    name = "runtime_data",
    srcs = glob([
        "conf/**",
        "scripts/**",
    ]),
)

apollo_package()

cpplint()
