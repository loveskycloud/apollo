# 二进制包验收记录（2026-10-07，格式 3）

新版包删除 `bazel-bin` 和运行库哈希目录；`lib/runtime` 平铺，每个公共
SONAME 选用一个实现。系统与 SDK OpenSSL 统一为系统 SSL/crypto 配对，
检查实际导入的符号及版本。真实公共库冲突会导致打包失败。
实际重名的模块私有库限定 SONAME，并同步改写 DT_NEEDED。

21 项打包回归测试、3 项 CLI 集成测试通过。覆盖不兼容库选择被拒绝、
相同字节但不同依赖的库被拒绝、同进程 RTLD_GLOBAL 加载不同私有模块、
带 70 个重名私有依赖的程序直接执行，以及移动路径、runfiles 和 CLI 发现。

## 验收结果

| 类型 | 大小 | 目标 | ELF | 校验文件 | 实际 PNC 仿真 |
| --- | ---: | ---: | ---: | ---: | --- |
| simulation / CPU | 635.5 MiB | 146 | 745 | 2336 | 3 秒，10 通道，1030 条消息 |
| all / GPU | 3795.1 MiB | 466 | 10013 | 39091 | 3 秒，10 通道，1030 条消息 |

两个包在普通用户 UID 1000、无挂载、无网络的 Apollo 容器中完成验收。
`lib/runtime` 分别有 489 / 1055 个库文件，没有子目录。所有 ELF 的依赖检查通过；
除 glibc/加载器和宿主 NVIDIA 驱动外，不允许解析到包外库。直接运行 Cyber C++ / Python
工具及 simulator_main，未修改解压后的程序或库来修补问题。
全量包额外在一个进程内使用 `RTLD_NOW | RTLD_GLOBAL` 加载 16 个私有库，
全部符号解析成功，获得 16 个不同加载句柄。结果见
[all/private-libraries.json](all/private-libraries.json)。这些库分别服务于 lidar、
radar4d 和不同过滤器，其 API 有不同的命名空间或类名，不能当成公共库的两个版本。
两个压缩包的 SHA-256 均通过，完整元数据见 [summary.json](summary.json)。

镜像：`registry.baidubce.com/apollo/apollo-env-gpu:10.0-u22`。
镜像 ID：`sha256:7c33b27bf30bccf5c13f4d1058c72437930681b646ee0b7bfece979f482a2530`。
GPU 验收容器额外指定 `--gpus all`；CPU 验收容器没有 GPU 请求。
包的适用范围为相同架构、兼容 Ubuntu ABI 的 Apollo 镜像；全量 x86 包
需要 SSE4.1 / AVX2，GPU 模块需要宿主 NVIDIA 驱动。

## 构建命令

在开发容器内执行，三个 include 是本次实际仿真验收使用的数据：

```bash
source modules/simulation/setup.bash
gen_binary.py --type simulation --output binary.tar.gz --jobs 6 \
  --include data/map_data/1haolou_202608241047qh \
  --include profiles/ranger_mini_v3 \
  --include data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
gen_binary.py --type all --output binary-all.tar.gz --jobs 6 \
  --include data/map_data/1haolou_202608241047qh \
  --include profiles/ranger_mini_v3 \
  --include data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
```

## 验收命令

直接复制、解压压缩包后运行；包含空格的解压路径验证可迁移性。

```bash
docker exec -u 1000 apollo-binary-layout-cpu-20261007 \
  python3 /tmp/verify_binary.py '/tmp/final unified cpu package v3/binary' \
  --map data/map_data/1haolou_202608241047qh \
  --vehicle-profile profiles/ranger_mini_v3 \
  --scene data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json

docker exec -u 1000 apollo-binary-layout-gpu-20261007 \
  python3 /tmp/verify_binary.py '/tmp/final unified gpu package v3/binary' \
  --map data/map_data/1haolou_202608241047qh \
  --vehicle-profile profiles/ranger_mini_v3 \
  --scene data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
```

## 保存的证据

每种类型的目录中保存 `result.json`、`container.json`、工具及仿真日志、
`manifest.json.gz`、`ldd.log.gz`、目标列表及压缩包 SHA-256。
全量目录另保存 `private-libraries.json` 和 `private-libraries.log`。
`manifest.json` 的 `library_sources` / `library_compatibility` 记录选用的库
来源和符号检查；`private_library_names` 记录需要限定名字的模块私有库。

真实运行验收覆盖 Cyber 和 PNC 仿真；传感器设备、其他 GPU 算法及
Dreamview 业务流程未逐项运行。10 月 6 日的记录对应旧格式，保留供对比。

## 使用已验证的仿真包

本次仿真包另外复制到宿主机 `/home/wangsheng/workspace/binary-v3.tar.gz`。
全量包另外复制到 `/home/wangsheng/workspace/binary-all-v3.tar.gz`。
在挂载该工作区的 Apollo 容器中，进入挂载目录后执行：

```bash
mkdir -p package-v3
tar -xzf binary-v3.tar.gz -C package-v3
source package-v3/binary/setup.bash
cyber_recorder info --help
```
