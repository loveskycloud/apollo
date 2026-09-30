# 2026-09-30 小车源码部署与模型配置

目标：nvidia@172.20.170.119:/home/nvidia/application-core-0921/modules/simulation/ml_planning

仅部署本模块源码、DAG、测试/训练工具和模型文件，不部署 x86 二进制、仿真器、Web Monitor，不启动车辆进程。目标机器为 aarch64，由用户在其 Apollo 构建环境执行：

```sh
buildtool build -p modules/simulation/ml_planning --cpu -j 4
```

默认配置为 `model_version: "v4"`，加载 `models/v4/unified.weights`（round4）。V3、V4 权重随 BUILD 安装，不再设置模型环境变量，不使用 `current/latest` 软链接。DAG 读取模型配置和 `modules/common/data/global_flagfile.txt`，后者沿用 `em map use beijing_zongyuan_1haolou` 设置的地图和已有车辆 profile。

本次只同步源码，不替换小车已安装的二进制，不自动编译或启动。请先在小车 Apollo 容器的 `/apollo_workspace` 执行上述构建，再使用：

```sh
cd /apollo && /opt/apollo/neo/bin/mainboard -d /apollo/modules/simulation/ml_planning/dag/ml_planning.dag
```

唯一运行配置文件为 `/apollo/modules/simulation/ml_planning/conf/ml_planning.pb.txt`。例如切换到已安装的 V3：

```sh
printf 'model_version: "v3"\n' > /apollo/modules/simulation/ml_planning/conf/ml_planning.pb.txt
```

然后重启 ML Planning。恢复 V4 将上面 `v3` 改为 `v4`。添加 V5 时先放置真实权重到 `/apollo/modules/simulation/ml_planning/models/v5/unified.weights`，再修改版本；当前未提供 V5。每个版本应保留独立权重文件，不覆盖正在使用的版本。重建会重新安装源码默认配置，请核对实际运行配置。

组件依赖现有定位、感知、external_command 输出的 `/apollo/planning/command`、HDMap 和正确车辆 profile。小车继续启动原有 external_command；无需为 ML 额外启动 raw Routing。不要同时运行标准 Planning 与 ML Planning 向 `/apollo/planning` 发布轨迹。启动日志会显示实际加载的版本与文件路径；配置或模型不存在时直接报错。

当前组件限定 Ranger 0.72 × 0.50 m 车体，尚未完成实车及全部失败场景验收。保留对仍安全的上一帧轨迹的继续行驶选择，避免新的停车候选直接覆盖它。试验性的空间搜索/独立速度兜底已撤出，存档在开发机 experimental-before-vehicle-deploy；LQR/MPC 为后续待办，未实现。

此前综合修复试验曾使 2 个失败场景通过，但本次撤出试验分支后的独立组合尚未重新仿真；空轨迹缺陷仍未解决。本次操作仅完成源码交付，不代表可安全实车运行。

## 2026-09-30 标准命令接口更新

仿真与实车统一接收 PlanningCommand；新增任务状态、目标限速、Pad 停止/恢复/清除和 TemporaryStopCommand。运行时无效/不支持的输入打印 ERROR、反馈失败，进程继续运行并接受新任务。接口能力及原生验证命令见 README。权重未变；本次不是重新训练，也未解决此前所有空轨迹/停滞缺陷。仅同步 ml_planning 源码，用户自行编译并安排重启，未在线替换或重启小车进程。
