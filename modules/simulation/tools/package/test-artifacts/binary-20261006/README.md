# 二进制包验收记录（2026-10-06）

两个最终压缩包均由 `gen_binary.py` 编译当前工作区后生成，验收时直接解压，
未修改包内文件来补救依赖或配置。具体数据见 [summary.json](summary.json)。

| 类型 | 压缩包 | 大小 | 构建目标 | ELF 检查 | SHA-256 文件检查 |
| --- | --- | ---: | ---: | ---: | ---: |
| simulation / CPU | `binary.tar.gz` | 638.4 MiB | 146 | 747 通过 | 2338 通过 |
| all / GPU | `binary-all.tar.gz` | 3923.7 MiB | 466 | 10055 通过 | 39133 通过 |

镜像：`registry.baidubce.com/apollo/apollo-env-gpu:10.0-u22`。
镜像 ID：`sha256:7c33b27bf30bccf5c13f4d1058c72437930681b646ee0b7bfece979f482a2530`。
两个容器均为 `Mounts=[]`、`NetworkMode=none`，`/apollo_workspace` 为空。
全量容器通过 `--gpus all` 使用 NVIDIA 驱动，CPU 容器没有 GPU 请求。
镜像原有 Boost 目录保留；动态依赖检查不允许使用任何包外 SDK 库。

验收由 UID 1000 运行，环境只从包内 `setup.bash` 获取，目录含空格。
CPU 容器在 `/tmp` 外没有文件修改；全量容器在 `/tmp` 外的变更由 NVIDIA
容器运行时注入驱动及更新缓存产生，详见 summary。压缩包外部 SHA-256 也已通过。

## 构建命令

在 Apollo 开发容器内的工作区根目录执行：

```bash
source modules/simulation/setup.bash
gen_binary.py --type all \
  --output binary-all.tar.gz --jobs 6 \
  --include data/map_data/1haolou_202608241047qh \
  --include profiles/ranger_mini_v3 \
  --include data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json

gen_binary.py --type simulation --jobs 6 \
  --include data/map_data/1haolou_202608241047qh \
  --include profiles/ranger_mini_v3 \
  --include data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
```

程序构建、库收集、RPATH 重写及压缩均成功。14 项回归测试通过，覆盖同名
不同 ABI 的库、相同库内容在不同 `$ORIGIN` 下的依赖、话题名保护、路径迁移、
文件冲突、相对链接与标准 gzip / tar 解压。

```bash
python3 -m unittest discover -s modules/simulation/tools/package -p test_gen_binary.py -v
sha256sum -c binary.tar.gz.sha256 binary-all.tar.gz.sha256
```

## 验收命令

容器从上面的镜像创建，未挂载源码、Bazel 缓存或 SDK 卷。
全量容器使用 `--gpus all`；两个包分别复制、解压到以下路径后执行：

```bash
docker exec -u 1000 apollo-binary-verify-20261006 \
  python3 /tmp/verify_binary.py '/tmp/final simulation package/binary' \
  --map data/map_data/1haolou_202608241047qh \
  --vehicle-profile profiles/ranger_mini_v3 \
  --scene data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json

docker exec -u 1000 apollo-binary-all-verify-20261006 \
  python3 /tmp/verify_binary.py '/tmp/final all package/binary' \
  --map data/map_data/1haolou_202608241047qh \
  --vehicle-profile profiles/ranger_mini_v3 \
  --scene data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
```

验证器执行全部文件校验、包内链接检查、全部 ELF 的 `ldd`、
`cyber_recorder info --help`、`cyber_channel list --help` 和真实 `simulator_main`。
已解析的动态库仅允许包内文件、基础 glibc 或 NVIDIA 驱动。
验证器源码见 [verify_binary.py](../../verify_binary.py)。

两个包均启动 Prediction、Planning、Control、Routing，没有跳过算法模块。
3 秒仿真结束成功，录包 `is_complete=true`、10 个通道、1030 条消息：

| 通道 | 消息数（每个包） |
| --- | ---: |
| `/apollo/planning` | 31 |
| `/apollo/prediction` | 31 |
| `/apollo/control` | 301 |
| `/apollo/localization/pose` | 301 |
| `/apollo/canbus/chassis` | 301 |
| `/apollo/perception/obstacles` | 31 |
| `/apollo/planning/command_status` | 31 |
| `/apollo/raw_routing_request` | 1 |
| `/apollo/raw_routing_response` | 1 |
| `/apollo/planning/command` | 1 |

CPU 录包 2265787 字节，全量录包 2265729 字节。

## 保存的证据

- [simulation/result.json](simulation/result.json)、[all/result.json](all/result.json)：验证结果和录包通道统计。
- 两个目录的 `simulation.log`、`record-info.log`、`cyber-help.log`、`cyber-python-help.log`：直接运行日志。
- `manifest.json.gz`：压缩后的原始包 manifest；包内仍是普通 `manifest.json`。
- `ldd.log.gz`：全部 ELF 的实际依赖解析结果。
- `archive.targets.txt`、`archive.sha256`：原始目标列表及压缩包校验和。

基础镜像与构建环境须具有相同架构、兼容 Ubuntu ABI。全量 x86 包需要
SSE4.1 / AVX2；GPU 模块需要宿主 NVIDIA 驱动。此次运行覆盖 Cyber 与 PNC
仿真，未逐项运行传感器设备、其他 GPU 算法及 Dreamview 业务流程。
