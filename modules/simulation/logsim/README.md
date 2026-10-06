# LogSim

基于 Apollo Cyber record 的开环仿真。回放日志输入，重新运行 Localization、Perception、Prediction、Planning、Control 等模块，输出可回放的 record，用于问题复现和算法回归。

未启用 Localization 时使用日志中的 pose；启用后从日志中的 GNSS、IMU 输入重新计算 pose。新的规划和控制输出不会改变车辆运动。需要闭环验证时使用 [WorldSim](../worldsim/README.md)。

## 运行流程

```text
Cyber record → 时间窗口与通道过滤 → 仿真时钟 → 算法模块 → 输出 record
```

- 多个 record 按记录时间合并；选中的消息在启动时载入内存。
- 使用纳秒仿真时钟，按事件顺序运行，无实时等待；同一时刻先处理日志输入，再执行算法定时器。
- 模块通过 DAG 加载，在同一进程内同步执行。每轮回调完成后才推进时间。
- 原日志中被重算模块的输出不注入算法，按清单映射到 `/bag/...` 保留；输出包同时保存注入输入和新生成的算法输出。

`simulator_main` 与 `logsim_main` 使用同一运行器，由 `task.pb.txt` 的 `input_kind: BAG` 选择 LogSim；省略该字段时默认也是 `BAG`。

## 环境要求

- Apollo 开发容器，已安装所选模块及其插件、模型。
- 有效的 Cyber record，以及与日志匹配的地图、车辆参数和模块配置。
- 以下命令在容器内的 Apollo 工作区根目录执行。

```bash
cd /apollo_workspace
buildtool build -p modules/simulation --cpu
```

## Web Monitor

在 [Web Monitor](../web_monitor/README.md) 的 **Sim** 页面中：

1. 选择 Bag 输入、record、地图和车辆配置。
2. 选择需要重算的模块、日志时间范围和重复次数。
3. 提交任务，在结果中查看运行日志、比较结果，并通过 **Replay bag** 回放输出。

任务使用独立配置快照。输入通道按模块选择自动调整：未重算 Localization、Perception、Prediction 或 Planning 时，使用日志中对应的输出作为下游输入。规划需要的命令及其历史通道也会被注入。

## 命令行

### 创建任务

将示例路径替换为实际文件。多个 record 可重复传入 `--record-path`。

```bash
python3 modules/simulation/logsim/tools/gen_simulation_task.py \
  --scenario-id regression_demo \
  --task-dir /tmp/logsim_demo \
  --record-path /path/to/input.record.00000 \
  --map-dir /path/to/map \
  --vehicle-config-path /path/to/vehicle_param.pb.txt \
  --override-runtime-modules PREDICTION,PLANNING,CONTROL
```

默认重算 Prediction、Planning、Control。输入、屏蔽和录制列表均按 manifest 与所选模块生成，核心日志输入为：

| 日志输入 | 用途 |
| --- | --- |
| `/apollo/perception/obstacles` | 感知障碍物 |
| `/apollo/localization/pose` | 自车定位 |
| `/apollo/canbus/chassis` | 底盘状态 |

默认屏蔽并重新录制 `/apollo/prediction`、`/apollo/planning`、`/apollo/control`。

规划命令和历史命令自动加入输入列表；历史命令注入 `/apollo/planning/command`。截取日志时，时间窗口需包含算法所需的命令和初始化输入。

### 重算定位

在 `--override-runtime-modules` 中加入 `LOCALIZATION`，例如 `LOCALIZATION,PREDICTION,PLANNING,CONTROL`。默认使用仓库 `localization.launch` 对应的 RTK DAG：

| 输入 | 类型 |
| --- | --- |
| `/apollo/sensor/gnss/odometry` | `Gps` |
| `/apollo/sensor/gnss/corrected_imu` | `CorrectedImu` |
| `/apollo/sensor/gnss/ins_stat` | `InsStat` |
| `/tf_static` | 静态外参 |

输出为 `/apollo/localization/pose`、`/apollo/localization/msf_status` 和 `/tf`。同名原始消息只保存在 `/bag/...`，不进入算法输入。

初始化会预加载回放起点及之前的静态 TF；请包含记录外参的原始分段。缺少 GNSS/IMU/INS 输入、静态 TF 或没有生成新 pose 时，任务失败。`LOCALIZATION` 仅适用于 Bag 输入；WorldSim 直接生成车辆状态。

`rtk_localization.pb.txt` 的 `imu_frame_id` 和 `broadcast_tf_child_frame_id` 必须与录包外参一致，例如录包使用 `novatel` 时，将 `imu_frame_id` 配为 `novatel`。

### 重算感知

加入 `PERCEPTION`，例如 `PERCEPTION,PREDICTION,PLANNING,CONTROL`。任务将当前配置的 `modules/perception/launch/perception_lidar.launch` 合并为独立 DAG。Web Monitor 沿用车辆 profile 中的 launch 和组件配置；包含哪些感知子模块，就启动哪些。

传感器输入按实际 DAG 生成，支持 `PointCloud`、`Image`、`ContiRadar` 和 `OculiiPointCloud` 类型，并录制 pose、`/tf_static` 和 `/tf`。传感器输入保留原 topic，直接注入与录制，不生成 `/bag/` 副本。传感器名、外参和模型须与日志一致；检测模型需要对应的 GPU 环境。

启用后，原始 `/apollo/perception/obstacles` 不进入算法，只保存在 `/bag/apollo/perception/obstacles`；新的障碍物供 Prediction、Planning 使用。未启用时使用日志中的障碍物。与 Localization 同时启用时使用重新生成的 pose 和 TF。

CLI 可用 `--perception-launch /path/to/perception.launch` 指定已有 launch 或 DAG；任务服务支持同名配置项 `perception_launch`。任务保存实际输入、输出清单与 DAG，多组件 DAG 中每个 `module_config` 使用自己的动态库。缺少所需传感器输入、TF 或未生成障碍物消息时，任务失败。`PERCEPTION` 仅支持 Bag；WorldSim 直接生成障碍物。

### 原始通道对比

manifest 中的 `/bag` topic 声明原始副本，例如：

| 当前运行通道 | 原始日志副本 |
| --- | --- |
| `/apollo/localization/pose` | `/bag/apollo/localization/pose` |
| `/apollo/planning` | `/bag/apollo/planning` |
| `/apollo/perception/obstacles` | `/bag/apollo/perception/obstacles` |
| `/apollo/control` | `/bag/apollo/control` |

副本保留原消息字节、protobuf schema 和记录时间戳，不作为算法输入。回放输出包时，可在消息检查器或曲线工具中选择两套 topic，对比 pose 的位置和航向字段。

### 运行任务

```bash
/opt/apollo/neo/bin/simulator_main --task_dir=/tmp/logsim_demo
```

运行感知或检测时，将 `APOLLO_MODEL_PATH` 设为实际模型目录，例如 `export APOLLO_MODEL_PATH=/opt/apollo/neo/share/modules/perception/data/models`。Web Monitor 自动使用任务配置中的模型目录。

默认输出路径为 `data/simulation/<scenario_id>/sim_run_<timestamp>.record*`。Cyber 会为实际文件追加分段后缀，请使用生成的完整文件名。

### 重复运行与比较

```bash
python3 modules/simulation/logsim/tools/launch_job.py \
  --task-dir /tmp/logsim_demo --repeat 2 --compare
```

结果保存在任务目录的 `output/`：

| 文件 | 内容 |
| --- | --- |
| `sim_run_0.record*`、`sim_run_1.record*` | 两次运行的输出 |
| `compare.json` | 比较摘要及前 20 条差异 |

比较要求消息顺序、记录时间戳和 protobuf 值完全一致；规范化 protobuf map 顺序，仅排除 Planning、Control 中明确列出的实际耗时统计字段。原始差异保留在 `raw_result` 中。`PASS` 表示同一环境下重复运行一致，不代表算法行为正确。

手动比较两个输出包：

```bash
python3 modules/simulation/logsim/tools/bag_diff.py \
  --left /path/to/run_a.record.00000 \
  --right /path/to/run_b.record.00000 \
  --output /tmp/compare.json --algorithm
```

去掉 `--algorithm` 可比较原始消息字节；添加 `--details --limit 10` 可导出字段级差异。摘要比较通过返回 `0`，失败或出错返回非零。

## 配置

### 录制清单

[simulation.manifest.json](../simulator/conf/simulation.manifest.json) 定义需要录制的 topic，分为两部分：

```json
{
  "modules": [
    {
      "name": "PLANNING",
      "inputs": ["/apollo/prediction", "/bag/apollo/prediction"],
      "outputs": ["/apollo/planning", "/bag/apollo/planning"]
    }
  ],
  "common": ["/apollo/localization/pose", "/bag/apollo/localization/pose"]
}
```

- `modules`：每个模块要录制的输入和输出，分别为 topic 列表。
- `common`：始终录制的公共 topic，例如 pose 和底盘。
- `/bag/...`：显式声明的原始 topic 副本，映射规则为原 topic 前加 `/bag`。感知传感器输入只录制原 topic；WorldSim 不录制这些副本。
- 录制集合为 `common` 与所选模块的 `inputs`、`outputs` 并集，去重；运行器另外自动录制注入通道。
- 清单声明录制范围与模块输入、输出；模块启停由任务选择决定。任务生成时，根据所选模块的输出生成 bag 输入屏蔽列表。

命令行生成器和 Web Monitor 均从清单生成 `record_channels`，并在任务目录保存 `simulation.manifest.json` 快照。修改源清单后需创建新任务；已生成的任务继续使用其 `task.pb.txt`。

### 任务参数

在任务目录的 `task.pb.txt` 中修改配置，字段定义见 [simulation_task.proto](proto/simulation_task.proto)。

| 字段 | 说明 |
| --- | --- |
| `record_paths` | 输入 record 文件列表 |
| `log_start_s`、`log_end_s` | record 的绝对时间戳，单位秒；`0` 表示该侧不限制，非日志起点偏移 |
| `map_dir`、`vehicle_config_path` | 地图目录和车辆参数文件 |
| `runtime_modules`、`dag_paths` | 运行模块及对应 DAG，顺序须一致 |
| `profile_path` | 可选运行配置根目录；设置后，相对路径从该目录解析 |
| `channel_policy.inject_channels` | 从日志注入的通道 |
| `channel_policy.suppress_channels` | 禁止从日志注入的通道；优先于注入列表 |
| `channel_policy.record_channels` | 需要录制的通道；注入通道也自动录制 |
| `channel_policy.bag_topic_mappings` | 原始 topic 到 `/bag/...` 的录制映射，独立于注入与屏蔽列表 |
| `output_record_dir` | 默认输出根目录，默认 `data/simulation` |
| `output_record_path` | 指定输出文件前缀，覆盖目录命名规则 |

输出路径优先级：`SIM_OUTPUT_RECORD` 环境变量 > `output_record_path` > `output_record_dir`。重复运行脚本使用环境变量将输出写入任务目录。

生成器同时创建 `scenario.pb.txt`；该文件存在时，模块、地图和车辆配置优先从中读取，修改时应与 `task.pb.txt` 保持一致。生成的 `runtime.gflags` 和 `modules.dag.list` 不会被运行器自动加载。

命令行与 Web Monitor 使用相同的通道策略：所选模块的输出被屏蔽，其余所需输入从日志注入。例如仅重算 Control 时，自动使用 bag 中的 planning 和 pose。

## 限制与排查

- 仅支持 `sim_mode: DEFAULT`；`ALIGNED`、`DECOUPLED` 和 `enable_onboard_latency: true` 会报错。`frame_meta_path`、`channel_mapping_path` 尚未接入。
- 算法输入与新输出录制需要绑定 protobuf 类型，见 [message_consumer.cc](../simulator/message_consumer.cc) 和 [output_channel_recorder.cc](../simulator/output_channel_recorder.cc)。`/bag/...` 副本直接使用原 record 的 schema 和消息字节。
- LogSim 不执行 WorldSim 的物理碰撞检查，碰撞结果为 `NOT_EVALUATED`。
- 大日志会占用较多内存，建议通过时间窗口缩小复现范围。

| 问题 | 检查项 |
| --- | --- |
| `No messages in requested record window / channel selection` | record 时间范围、输入通道是否匹配 |
| `no typed publisher` / `no typed binding` | 通道是否有对应类型绑定 |
| 模块或插件加载失败 | 所选模块、插件、模型是否安装，DAG 和配置路径是否有效 |
| Planning 无有效输出 | 规划命令、上游输入、地图与车辆配置是否齐全且匹配 |
| 重复比较失败 | 查看 `compare.json`，回放输出包并定位具体差异 |

`--skip_modules`、`--skip_ego_env` 仅用于运行器排查，不能作为算法验证结果。
