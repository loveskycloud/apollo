# 不同 Binary 的仿真执行

验收目标：在 Apollo 容器内，通过同一条 `gen_simulation_task.py | launch.py`
流水线分别运行本地 binary 和服务 ID 对应的 binary，算法/结果分析正常，失败显式返回。

- [x] `binary.py` 定义 Binary 基类、本地/下载对象、路径及版本指纹。
- [x] `get_binary(-1)` 使用指定目录、source 的 binary 环境或本地打包产物。
- [x] `get_binary(ID)` 使用可替换 Provider；HTTP 服务、原子缓存及 SHA-256 校验。
- [x] 解压检查包外路径/链接；缓存损坏和下载失败显式报错。
- [x] apps 中增加生成和 launch 入口，stdout 仅传递版本化 JSON。
- [x] 通过独立环境/进程复用所选包的 TaskService，传递 Binary 给 run_simulation。
- [x] `-i` 预留场景 ID 服务，当前用 `-f`；cluster 后端显式预留。
- [x] 构建数据清单收集 CLI，实现 source 二进制包后直接调用。
- [x] 网络下载/缓存/解压、环境隔离、任务协议、失败/中断测试（17 项）。
- [x] 重建 CPU 包，实际验收包内 CLI 与 source 的命令发现；3564 文件/687 ELF 检查通过。
- [x] 干净容器：本地 CPU v7 与服务 ID 的全量 v6，各运行 PNC、ML、静态避障并重复两次，12 次原生运行通过。
- [x] 六个 CLI 任务的实际录包经 MCAP 转换、Topic 解码及 gRPC 回放通过。
- [x] 保存任务、选中 binary 指纹、服务请求、实际录包/冻结模型/分析及失败证据，完成使用文档。

验收和交付见 [2026-10-07 记录](VALIDATION.md)。
后续扩展：连接正式 BinaryProvider / 场景 ID 服务，实现 ClusterLauncher；
当前 `-i` 和 `-c cluster` 明确报未连接，不计入已实现功能。
