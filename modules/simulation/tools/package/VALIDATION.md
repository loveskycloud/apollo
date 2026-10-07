# 二进制打包历史验收摘要

2026-10-07 已完成以下验收。按用户要求，测试 binary、历史分发压缩包、
test-artifacts 中的录包/模型副本/MCAP/日志及旧测试容器已清理；此文件仅保留结论。
复验时重新生成包，使用现有测试和验收脚本获取新的结果。

## 打包与 CLI

- 从任意工作目录 source 仿真环境后直接执行 gen_binary.py。
- 3 项 CLI 集成测试通过；Cyber Recorder 小包真实构建、解压、source、运行通过
  （1 目标、104 ELF、29.9 MiB）。
- v6 CPU：147 目标、687 ELF、3556 校验文件、399 个包内运行库、94 项镜像库。
- v6 all：467 目标、9920 ELF、40276 校验文件、931 个包内运行库、127 项镜像库。
- 包只包含顶层 bin，lib/runtime 平坦，无 bazel-bin、空运行库目录或包外链接。
- 镜像中的系统库和明确声明的 Apollo SDK OpenSSL 配对由镜像提供，其他运行依赖随包。
- 完整 SHA、ELF 依赖、Cyber C++/Python 工具、Web Monitor 页面资源及 HTTP/gRPC 检查通过。
- 29 项打包、29 项任务服务及相关分析测试、四个相关 C++ 测试目标通过。
  额外旧 pnc_path_test 的空 lane fixture 曾崩溃，此处不宣称全仓库测试通过。

## WorldSim

两个无挂载、无网络的干净 Apollo 容器由 UID 1000 运行。镜像为
registry.baidubce.com/apollo/apollo-env-gpu:10.0-u22。
CPU/all 各经 Web Monitor API 执行 PNC、ML 无障碍和 ML 静态避障，每项重复两次，
共 12 次原生运行，录包、模块输出、碰撞/连续性/到达/质量、严格确定性及回放通过。

PNC 运行 Prediction/Planning/Control/Routing、kinematic_control，健康规划帧 354，
配置停车目标误差 0.304828 m。Control 最初 100 ms 的 10 帧等待轨迹状态保留，
之后正常；要求真实停车决策及 mission_complete。
ML 运行 ML Planning/Routing、perfect_planning，覆盖理想轨迹跟随：
无障碍/静态避障分别 388/403 健康规划帧、0.235757/0.238289 m 目标误差，
独立 actor 推理误差约 6e-15/9.33e-15，阈值 1e-8。
真实 record 经 MCAP 转换、Planning/Localization Topic 解码和 gRPC 回放窗口检查通过。

处理过的根因包括任务服务/schema/decoder/转换依赖漏包、Planning mock 时钟起步、
地图末端投影越界、OSQP 根据真实耗时更新 rho 导致重复差异，以及标准 Destination
停车语义。算法修复和验证脚本保留在源码中。

用法和复验命令见 [BINARY_PACKAGING.md](BINARY_PACKAGING.md)；
按 Binary 运行 CLI 的后续验收见 [执行验收摘要](../execution/VALIDATION.md)。
