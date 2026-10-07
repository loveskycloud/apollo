load("//tools:cpplint.bzl", "cpplint")
load("//tools:apollo_package.bzl", "apollo_cc_library", "apollo_cc_binary", "apollo_cc_test", "apollo_package")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "task_service_runtime",
    srcs = [
        "configuration_tools.py",
        "driving_quality.py",
        "parking_interaction_metrics.py",
        "parking_metrics.py",
        "parking_scorecard.py",
        "parking_trigger_metrics.py",
        "planning_continuity.py",
        "quality_metrics.py",
        "result_analysis.py",
        "scenario_evaluation.py",
        "task_service.py",
        "//modules/simulation/logsim:task_service_helpers",
        "//modules/simulation/ml_planning:model_selector",
        "//modules/simulation/ml_planning/proto:ml_planning_config_proto_py_pb2",
        "//modules/simulation/worldsim/proto:scenario_proto_py_pb2",
        "//cyber/proto:dag_conf_proto_py_pb2",
        "//cyber/proto:proto_desc_proto_py_pb2",
        "//modules/common_msgs/config_msgs:vehicle_config_proto_py_pb2",
        "//modules/common_msgs/control_msgs:control_cmd_proto_py_pb2",
        "//modules/common_msgs/localization_msgs:localization_proto_py_pb2",
        "//modules/common_msgs/map_msgs:map_proto_py_pb2",
        "//modules/common_msgs/perception_msgs:perception_obstacle_proto_py_pb2",
        "//modules/common_msgs/planning_msgs:planning_proto_py_pb2",
        "//modules/common_msgs/routing_msgs:routing_proto_py_pb2",
        "//modules/planning/traffic_rules/destination/proto:destination_proto_py_pb2",
    ],
)

apollo_cc_library(
    name = "apollo_simulation_event",
    hdrs = [
        "i_message_source.h",
        "sim_event.h",
    ],
)

apollo_cc_library(
    name = "apollo_simulation_core",
    srcs = [
        "bag_diff_comparator.cc",
        "class_controller.cc",
        "computational_graph.cc",
        "dag_controller.cc",
        "ego_car.cc",
        "environment_tools.cc",
        "emulator_controller.cc",
        "emulator_message_queue.cc",
        "message_buffer.cc",
        "message_consumer.cc",
        "module_catalog.cc",
        "module_replay_service.cc",
        "output_channel_recorder.cc",
        "periodic_trigger_generator.cc",
        "result_sink.cc",
        "scenario_util.cc",
        "sim_class_loader.cc",
        "sim_engine.cc",
        "sim_initializer.cc",
        "sim_monitor.cc",
        "sim_progress.cc",
        "sim_runner.cc",
        "sim_scheduler.cc",
        "task_queues.cc",
        "virtual_timer_scheduler.cc",
        "//modules/simulation/worldsim:world_message_source.cc",
    ],
    hdrs = [
        "bag_diff_comparator.h",
        "class_controller.h",
        "computational_graph.h",
        "dag_controller.h",
        "ego_car.h",
        "environment_tools.h",
        "emulator_controller.h",
        "emulator_message_queue.h",
        "i_message_source.h",
        "message_buffer.h",
        "message_consumer.h",
        "message_source_factory.h",
        "module_catalog.h",
        "module_instance.h",
        "module_replay_service.h",
        "output_channel_recorder.h",
        "periodic_trigger_generator.h",
        "result_sink.h",
        "scenario_util.h",
        "sim_class_loader.h",
        "sim_engine.h",
        "sim_event.h",
        "sim_initializer.h",
        "sim_monitor.h",
        "sim_progress.h",
        "sim_runner.h",
        "sim_scheduler.h",
        "task_queues.h",
        "virtual_timer_scheduler.h",
        "//modules/simulation/worldsim:world_message_source.h",
    ],
    deps = [
        ":apollo_simulation_event",
        "//cyber",
        "//cyber/class_loader:cyber_class_loader",
        "//cyber/proto:dag_conf_cc_proto",
        "//cyber/record:cyber_record",
        "//cyber/scheduler:cyber_scheduler",
        "//modules/common/configs:config_gflags",
        "//modules/common/configs:vehicle_config_helper",
        "//modules/common_msgs/config_msgs:vehicle_config_proto",
        "//modules/common_msgs/chassis_msgs:chassis_proto",
        "//modules/common_msgs/control_msgs:control_cmd_proto",
        "//modules/common_msgs/localization_msgs:localization_proto",
        "//modules/common_msgs/localization_msgs:gps_proto",
        "//modules/common_msgs/localization_msgs:imu_proto",
        "//modules/common_msgs/localization_msgs:localization_status_proto",
        "//modules/common_msgs/sensor_msgs:ins_proto",
        "//modules/common_msgs/sensor_msgs:pointcloud_proto",
        "//modules/common_msgs/sensor_msgs:sensor_image_proto",
        "//modules/common_msgs/sensor_msgs:conti_radar_proto",
        "//modules/common_msgs/sensor_msgs:oculii_radar_proto",
        "//modules/common_msgs/perception_msgs:traffic_light_detection_proto",
        "//modules/common_msgs/transform_msgs:transform_proto",
        "//modules/common_msgs/perception_msgs:perception_obstacle_proto",
        "//modules/common_msgs/planning_msgs:planning_proto",
        "//modules/common_msgs/planning_msgs:planning_command_proto",
        "//modules/common_msgs/planning_msgs:pad_msg_proto",
        "//modules/common_msgs/external_command_msgs:command_status_proto",
        "//modules/common_msgs/prediction_msgs:prediction_obstacle_proto",
        "//modules/common_msgs/routing_msgs:routing_proto",
        "//modules/map:apollo_map",
        "//modules/transform:apollo_transform",
        "//modules/simulation/logsim:apollo_simulation_logsim",
        "//modules/simulation/worldsim:worldsim_core",
        "//modules/simulation/ml_planning/proto:ml_planning_config_proto",
        "//modules/simulation/logsim/proto:simulation_task_proto",
        "//modules/simulation/simulator/proto:module_trigger_proto",
        "//modules/simulation/simulator/proto:scenario_proto",
        "@com_github_gflags_gflags//:gflags",
        "@com_google_protobuf//:protobuf",
    ],
)

apollo_cc_binary(
    name = "simulator_main",
    srcs = ["simulator_main.cpp"],
    data = [
        ":task_service_runtime",
        "//modules/simulation/tools/apollo_record_tools:apollo_record_tool",
        "//modules/simulation/tools/execution:cli_runtime",
    ],
    deps = [
        ":apollo_simulation_core",
        "//modules/simulation/logsim:apollo_simulation_logsim",
        "@com_github_gflags_gflags//:gflags",
    ],
)

apollo_cc_binary(
    name = "logsim_main",
    srcs = ["logsim_main.cpp"],
    deps = [
        ":apollo_simulation_core",
        "//modules/simulation/logsim:apollo_simulation_logsim",
        "//modules/simulation/logsim/proto:simulation_task_proto",
        "//modules/simulation/simulator/proto:scenario_proto",
        "@com_github_gflags_gflags//:gflags",
        "@com_google_protobuf//:protobuf",
    ],
)

apollo_cc_test(
    name = "emulator_message_queue_test",
    size = "small",
    srcs = ["emulator_message_queue_test.cc"],
    deps = [
        ":apollo_simulation_core",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

apollo_cc_test(
    name = "periodic_trigger_generator_test",
    size = "small",
    srcs = ["periodic_trigger_generator_test.cc"],
    deps = [
        ":apollo_simulation_core",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

apollo_cc_test(
    name = "message_buffer_test",
    size = "small",
    srcs = ["message_buffer_test.cc"],
    deps = [
        ":apollo_simulation_core",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

apollo_cc_test(
    name = "bag_diff_comparator_test",
    size = "small",
    srcs = ["bag_diff_comparator_test.cc"],
    deps = [
        ":apollo_simulation_core",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

apollo_cc_test(
    name = "module_replay_service_test",
    size = "small",
    srcs = ["module_replay_service_test.cc"],
    deps = [
        ":apollo_simulation_core",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

apollo_cc_test(
    name = "computational_graph_test",
    size = "small",
    srcs = ["computational_graph_test.cc"],
    deps = [
        ":apollo_simulation_core",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

apollo_cc_test(
    name = "sim_scheduler_test",
    size = "small",
    srcs = ["sim_scheduler_test.cc"],
    deps = [
        ":apollo_simulation_core",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

apollo_cc_test(
    name = "emulator_controller_test",
    size = "small",
    srcs = ["emulator_controller_test.cc"],
    deps = [
        ":apollo_simulation_core",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

apollo_cc_test(
    name = "ego_car_test",
    size = "small",
    srcs = ["ego_car_test.cc"],
    deps = [
        ":apollo_simulation_core",
        "@com_google_googletest//:gtest_main",
    ],
    linkstatic = True,
)

filegroup(
    name = "runtime_data",
    srcs = glob([
        "conf/**",
    ]),
)

apollo_package()
cpplint()
