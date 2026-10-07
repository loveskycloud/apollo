# Apollo 镜像依赖验收（2026-10-07，格式 4）

格式 4 遵循 WORKSPACE 的 OpenSSL 声明，选择
`/opt/apollo/pkgs/openssl/lib64/libssl.so.3` 与 `libcrypto.so.3` 配对。
两者由原始 Apollo 镜像提供，不重复打包；验收要求 SHA-256 一致。
全量消费者实际需要的 70 个 SSL / 676 个 crypto 符号及版本均满足。
此前系统 SSL 与 SDK crypto 混用造成的 OPENSSL_3.0.3 错误已消除。

系统库仅在干净镜像清单中存在同 SONAME、同路径时才省略。
清单来自 CPU 验收容器的 `/sbin/ldconfig -p`，包含 609 个条目。
开发容器额外安装的 GTK、OpenNI 等 17 个库仍打包。
一般系统库内容不一致时检查实际导入的符号和 GNU 符号版本。
26 项打包回归测试通过；CLI 接入的 3 项测试记录保留在 cli-20261006。

镜像：`registry.baidubce.com/apollo/apollo-env-gpu:10.0-u22`。
ID：`sha256:7c33b27bf30bccf5c13f4d1058c72437930681b646ee0b7bfece979f482a2530`。
验收容器无挂载、网络关闭，工作区为空，仅复制最终压缩包和验收脚本。
普通用户 UID 1000 在包含空格的路径下直接验收，程序和库没有验收修补。
GPU 容器通过 `--gpus all` 使用宿主 NVIDIA 驱动。

## 验收结果

| 类型 | 压缩大小 | 目标 | ELF | 校验文件 | 包内运行库 | 镜像库 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| simulation / CPU | 603.3 MiB | 146 | 652 | 2243 | 396 | 93 |
| all / GPU | 3635.9 MiB | 466 | 9886 | 38964 | 928 | 127 |

两个包均通过布局、SHA-256、全部 ELF 依赖、镜像库及 Cyber C++ / Python
工具检查，并完成 3 秒 PNC 仿真：1030 条消息、10 个通道；Planning /
Prediction 各 31 帧，Control / Localization 各 301 帧。
全量包还通过 16 个私有库的单进程加载检查。
仿真包有 80 个镜像库内容一致、13 个通过所需符号/版本检查；全量包对应
113 / 14 个。两个包的 Apollo OpenSSL 都内容一致，没有系统 SSL 混用。

已验证包交付到 `/home/wangsheng/workspace/binary-v4.tar.gz` 和
`binary-all-v4.tar.gz`，附同名 `.sha256`；交付副本校验通过。

```bash
cd /home/wangsheng/workspace
mkdir -p package-v4
tar -xzf binary-v4.tar.gz -C package-v4
source package-v4/binary/setup.bash
cyber_recorder info --help
```

## 构建与验收

在开发容器中执行，地图/profile/场景用于此次实际仿真：

```bash
source modules/simulation/setup.bash
gen_binary.py --type all --output binary-all.tar.gz --jobs 6 \
  --include data/map_data/1haolou_202608241047qh \
  --include profiles/ranger_mini_v3 \
  --include data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
gen_binary.py --type simulation --output binary.tar.gz --jobs 6 \
  --include data/map_data/1haolou_202608241047qh \
  --include profiles/ranger_mini_v3 \
  --include data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
```

最终压缩包复制并解压到对应干净容器后执行：

```bash
docker exec -u 1000 apollo-binary-layout-gpu-20261007 \
  python3 /tmp/verify_binary.py '/tmp/final apollo image gpu v4/binary' \
  --map data/map_data/1haolou_202608241047qh \
  --vehicle-profile profiles/ranger_mini_v3 \
  --scene data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
docker exec -u 1000 apollo-binary-layout-cpu-20261007 \
  python3 /tmp/verify_binary.py '/tmp/final apollo image cpu v4/binary' \
  --map data/map_data/1haolou_202608241047qh \
  --vehicle-profile profiles/ranger_mini_v3 \
  --scene data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
```

每种类型的目录保存 result、镜像库检查、容器条件、工具与仿真日志、
压缩后的 manifest / 全部 ELF 的 ldd 日志、目标列表及压缩包校验值。
全量目录还包含 16 个私有库在同一进程中以 RTLD_NOW / RTLD_GLOBAL
加载的证据。完整元数据见 [summary.json](summary.json)。

运行验收覆盖 Cyber 和完整 PNC 仿真；传感器设备、其他 GPU 算法及
Dreamview 业务流程未逐项运行。全量包需要 SSE4.1 / AVX2；GPU 模块
需要宿主 NVIDIA 驱动。目标镜像必须满足 manifest 的库要求。
