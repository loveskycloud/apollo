load("//tools:cpplint.bzl", "cpplint")
load("//tools:apollo_package.bzl", "apollo_cc_library", "apollo_cc_binary", "apollo_cc_test", "apollo_package")

package(default_visibility = ["//visibility:public"])

apollo_cc_library(
    name = "apollo_simulation_logsim",
    srcs = ["record_file_source.cc"],
    hdrs = ["record_file_source.h"],
    deps = [
        "//cyber",
        "//cyber/record:cyber_record",
        "//modules/simulation/simulator:apollo_simulation_event",
    ],
)

apollo_cc_test(
    name = "record_file_source_test",
    size = "small",
    srcs = ["record_file_source_test.cc"],
    deps = [
        ":apollo_simulation_logsim",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

apollo_package()
cpplint()
