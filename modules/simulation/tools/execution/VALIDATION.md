# 按 Binary 运行历史验收摘要

2026-10-07 验收完成。按用户要求，测试压缩包和 test-artifacts 的原始录包、
冻结模型、MCAP、详细 JSON/日志已清理；保留源码、测试、复验脚本和此摘要。

- 从 CPU v7 包 source 后，直接用 gen_simulation_task.py | launch.py。
- -B -1 使用本地 CPU v7；-B 123143 通过容器内 HTTP mock 下载全量 v6。
- 两个 binary 各运行 PNC、ML 无障碍、ML 静态避障，每项 repeat 2，共 12 次原生运行。
- 执行、所选程序/库路径、模块输出、算法健康、场景/质量、严格重复一致性均通过。
- 四个 ML 任务以真实观测和冻结权重独立推理比较通过，最大误差约 9.33e-15。
- 六个任务的实际 record 完成 MCAP 转换、Topic DebugString 解码和真实 gRPC 回放。
- 本地三个任务不访问 binary 服务；远端三个任务查询元数据三次，archive 只下载一次。
- worker 清除之前 binary 的运行环境，再 source 所选包；TaskService 和同目录 helper、
  schema、simulator_main、配置、模型及动态库来自选中环境。
- 17 项执行测试、3 项 CLI 测试、29 项打包回归通过。
- 新 CPU v7 的 3564 文件、687 ELF、94 项镜像库、Cyber 工具和 Web Monitor 检查通过。

验收容器无挂载、network none、仅 loopback HTTP，UID/GID 1000，使用 Apollo
apollo-env-gpu:10.0-u22 镜像，未安装额外 Python 包。
本地/下载 simulator_main 指纹分别为
3af839347f61857d46d53c0e39cc7fb63c9b4ddfcaf5994ee416cbe695cf72ba、
647ea2d9adee815c9a1c4f0e8b2a046c0b6cb62ee8d9b24971a89ff1f7a7ac1b。

PNC 使用 kinematic_control，Control 初始 100 ms 的已知等待状态保留，后续全部正常。
ML 使用 perfect_planning，覆盖理想轨迹跟随。静态障碍示例无 vehicleProfile，
必须显式指定 ranger_mini_v3 profile。首次动态加载遗漏同目录导入路径的问题已修复，
并在 fixture 中覆盖。-i 和 -c cluster 目前预留并明确报未配置。

复验使用 tests/native_pipeline.py 和 tests/native_replay.py，需先重新构建两个实际包。
对象、服务 API 和 CLI 用法见 [README.md](README.md)，实施清单见 [TODO.md](TODO.md)。
