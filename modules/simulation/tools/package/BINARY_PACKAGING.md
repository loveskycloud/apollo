# Apollo 二进制打包

`gen_binary.py` 直接编译当前源码，输出 `binary.tar.gz`；不走 SDK 安装流程。
默认 `simulation`，也可选择 `all`，类型可通过 JSON 配置扩展。
CLI 入口位于 `modules/simulation/apps/gen_binary.py`，业务实现、配置、测试、
验收脚本和记录位于 `modules/simulation/tools/package/`。

## 使用

Apollo 开发容器内，在工作区根目录运行：

```bash
source modules/simulation/setup.bash
gen_binary.py --type simulation
gen_binary.py --type all --output binary-all.tar.gz
```

`source cyber/setup.bash` 也会自动启用这些命令。source 后可以从任意目录
调用；默认产物位于工作区根目录，显式指定的相对 `--output` 按当前目录解析。
CLI 流程和其他 `gen_*.py` 的迁移方式见 [apps 说明](../../apps/README.md)。

`simulation` 默认 CPU，包含 Cyber 工具、Simulator/LogSim、普通和 ML Planning、
Control、Prediction、Routing、RTK 定位、相对地图、Transform、Web Monitor 及相应插件。
不包含感知、传感器驱动、Dreamview、训练/离线工具及数据集。
`all` 默认 GPU，选择 `//cyber/...` 和 `//modules/...` 中的生产 C++/Python
运行目标和插件，排除测试及 SDK 安装规则。所有选定目标必须编译成功；
不会静默跳过失败模块。可用 `--config cpu|gpu` 覆盖构建配置。
全量包中的 x86 相机驱动使用 AVX2，感知 i_lib 使用 SSE4.1；目标 CPU 应支持
这两种指令集，要求会记录在 manifest 中。

地图、场景、车辆 profile 按需加入：

```bash
gen_binary.py --type simulation \
  --include data/map_data/1haolou_202608241047qh \
  --include profiles/ranger_mini_v3 \
  --include data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json \
  --include modules/simulation/ml_planning/examples/1haolou/straight_centered.worldsim.scenario.json
```

外部数据使用 `--include /absolute/source=relative/package/path`。
压缩包保存到 `--output` 指定位置；同目录生成构建日志、目标列表和 SHA-256。
失败不会替换已有压缩包。工作区锁将多个打包进程串行执行，避免切换构建
配置时覆盖正在收集的输出。
`--jobs 6` 控制并发；`--bazel-arg=--flag=value` 传入额外 Bazel 参数。

## 运行

```bash
tar -xzf binary.tar.gz
source binary/setup.bash
cyber_recorder info --help
simulator_main --task_dir=/path/to/task
web_monitor_main
```

`setup.bash` 将工作目录切换到包根目录，配置程序/动态库/Python 路径及
Apollo 的配置、DAG 和插件索引路径。解压目录可以移动，也可以包含空格。
环境脚本依次配置包内运行库、明确声明的 Apollo 镜像库及 NVIDIA 驱动目录。
它不会继承旧工作区的 LD_LIBRARY_PATH；OpenSSL 使用项目声明的 Apollo SDK 路径。
现有任务文件中的地图、录包、profile 等外部绝对路径仍应根据目标容器更新。

包内包含 `bin/`、`lib/`、`cyber/`、`modules/`、`share/`、`setup.bash`、
`manifest.json` 和 `SHA256SUMS`。源码构建用到的外部符号链接被物化；包内
组件路径兼容链接是相对链接。动态组件保留模块路径，同名插件不会相互覆盖。
程序入口只有顶层 `bin/`，包中不生成 `bazel-bin`；launch 等运行资源中的构建
路径按输出映射改写到 `bin/` 或 `lib/`。环境和插件查找只使用包内运行目录。

动态依赖直接放在平铺的 `lib/runtime/<库名>` 中，不生成哈希子目录或
`variants`。公共库每个 SONAME 只选择一个实现；相同内容的副本合并，
不同内容必须由配置明确指定兼容来源，否则打包报错。相对 RPATH 只负责
找到包内文件，不能隔离同一进程中的同名库版本。

镜像已提供的系统运行库默认不重复打包。`system_library_roots` 声明镜像提供的
目录，且仅省略 `system_library_baseline` 清单中同 SONAME、同路径的库。
默认清单 `apollo_image_libraries.json` 来自无挂载的 Apollo 10 / Ubuntu 22.04
x86_64 原始镜像，包含 609 个系统库条目及镜像 ID；开发容器额外安装的
GTK、OpenNI 等库不在清单中，仍随包分发。更换镜像或架构时应重新采集
清单，未配置清单时不会仅凭 `/usr/lib` 路径省略库。
`image_libraries` 声明确切的特殊依赖，`library_overrides` 指定其来源。
两个默认类型依据 WORKSPACE 的 `boringssl` 声明，选择
`/opt/apollo/pkgs/openssl/lib64/` 中配对的 `libssl.so.3` / `libcrypto.so.3`。
干净 Apollo 镜像已包含这套库，所以包中省去 OpenSSL 副本。
系统和 SDK 的显示版本都为 3.0.2，但构建选项、符号版本不同；此前的
`OPENSSL_3.0.3` 错误来自系统 SSL 与 SDK crypto 混用，并不表示 SDK 配对不可用。

打包前检查所选库能提供使用方实际导入的 ELF 符号和 GNU 符号版本。
`manifest.json` 的 `image_libraries` 记录外部路径、SHA-256、所需符号及严格
版本要求。验收时特殊声明的 Apollo SDK 库必须内容一致；一般系统库若
校验值不同，需通过实际导入符号及版本检查，允许兼容的系统补丁更新。
缺库或缺少所需符号会明确报错。镜像未提供的第三方库仍随包分发。
当前配置构建的 Apollo 业务库优先于旧 SDK 的同路径副本。
`library_sources` 记录包内库来源，`library_compatibility` 记录选择时检查的符号数。

另有名字相同、功能不同的模块私有库，例如 lidar 和 radar4d 各自实现的
`libnms_cuda.so`。它们使用模块路径限定的 SONAME，并同步改写依赖方的
DT_NEEDED；它们不是同一公共库的两个版本。回归测试在同一进程中同时
加载两个模块，验证各自使用正确的私有实现。
库名限定只针对实际出现重名的模块库，其他库保留原 SONAME。重写时优先
选择容器中较新的 `patchelf`，一次改写同一 ELF 的全部依赖名，避免旧工具
多次扩展加载段。回归测试包含 70 个重名私有依赖的可执行程序直接启动。

支持与构建环境相同架构、满足 manifest 依赖要求的 Apollo 镜像。glibc、ELF
加载器和声明的镜像库由镜像提供，其余解析到的依赖随包收集。
GPU 包需要宿主机 NVIDIA 驱动及
`docker run --gpus all`；包中不分发宿主机驱动。

## 扩展类型

```bash
gen_binary.py --list-profiles
gen_binary.py --profiles-file /path/to/profiles.json --type routing-only
```

配置文件是以类型名为键的 JSON 对象。新类型无需修改脚本：

```json
{
  "routing-only": {
    "description": "Routing 运行包",
    "config": "cpu",
    "patterns": ["//modules/routing:librouting_component.so", "//cyber/mainboard:mainboard"],
    "resources": ["cyber", "modules/common", "modules/routing"]
  }
}
```

`patterns` 选定程序、组件和插件；运行时通过 `dlopen` 加载的插件必须在选择
范围内。`resources` 指定收集配置/DAG/launch/data/models/XML 的目录。
可用 `exclude_regex` 排除目标；`--target //package:label`（可重复）覆盖类型
的默认构建目标。依赖闭包使用与构建相同参数的 Bazel cquery 收集，
避免把不同 `select()` 分支的输出混装。
需要统一公共库来源时，可增加 `library_overrides`，例如
`"libcrypto.so.3": "/opt/apollo/pkgs/openssl/lib64/libcrypto.so.3"`；路径必须绝对，
`{multiarch}` 由运行 Python 的平台确定。使用 `image_libraries` 将该 SONAME
声明为镜像依赖，或使用
`system_library_roots` 声明一般系统库目录，并通过
`system_library_baseline` 指向从干净目标镜像采集的清单（相对配置文件解析）。
覆盖库必须保留原 SONAME 并提供
使用方需要的符号；发现不兼容时应修复构建依赖，不能强制忽略错误。

## 外部程序和运行数据

通过 exec/subprocess 启动的程序不在 ELF 的 DT_NEEDED 中，需显式声明运行依赖。
`runtime_dependencies` 按选中的主目标匹配，只构建/收集该程序所需的附加目标。
两个正式类型均声明 Web Monitor 的 forked Viewer、布局、任务服务及录包解码/转换工具：

```json
"runtime_dependencies": {
  "//modules/simulation/web_monitor:web_monitor_main": [
    "//modules/simulation/web_monitor:rerun_cli",
    "//modules/simulation/web_monitor:layout_files",
    "//modules/simulation/simulator:task_service_runtime",
    "//modules/simulation/tools/apollo_record_tools:apollo_record_tool",
    "//modules/simulation/tools/apollo_record_tools:web_monitor_runtime"
  ]
}
```

生成的 Rust ELF 放在 `bin/rerun`，参与同样的动态依赖、迁移和校验检查；
布局源文件保存到 `modules/simulation/web_monitor/layouts/`。使用 cquery 的
Starlark 输出获取全部数据文件，因为 Bazel 5 的 files 输出会省去源文件布局。
启动器优先按自己的位置查找 Viewer/布局，从其他工作目录启动也不依赖旧工作区。
它使用 exec 传递参数，优先选择包内 Viewer，缺失依赖时明确报错。
这里使用仓库构建的 Rerun fork；官方 pip 包不包含本项目的仿真和调试功能。

任务服务及辅助 Python 模块是运行产物，按明确的 filegroup 清单收集，保留
`modules/simulation/` 下的导入关系；测试、构建脚本和虚拟环境不进入清单。
录包解码程序使用本次 Bazel 构建的 Cyber，放入唯一的 `bin/apollo_record_tool`。
`setup.bash` 指定包内 `WEB_MONITOR_SIM_SERVICE`、`SIMULATOR_BINARY` 和工作区，
任务进程沿用包内程序、插件索引、生成的 Python schema 和运行库；任务配置和日志
写入私有目录。任务分析使用已验证 Apollo 镜像中的 NumPy 2.1.3 和 Shapely 2.0.6。

生成的 Python protobuf 及其导入闭包统一放入 `python/cyber/` 和 `python/modules/`，
包含 `external/apollo_src` 生成的 schema，解压后无需源码或 Bazel 缓存。
回放转换/调试脚本使用独立的 `lib/python/record_tools/` 依赖集合；打包阶段由 pip
从 `requirements-web-monitor-runtime.txt` 收集 wheel，并记录实际版本。
这不会覆盖镜像 Python，也不需要在目标容器中联网安装或搬运 `.venv`。
这些 wheel 的 ELF 同样参与动态依赖闭包、迁移和校验。
GPU 点云加速属于显式可选能力；本轮 PNC/ML 回放使用转换器的 CPU 路径。

`python_runtimes` 可按主目标配置额外 Python 运行依赖：

```json
"python_runtimes": {
  "//modules/simulation/web_monitor:web_monitor_main": {
    "requirements": "modules/simulation/tools/apollo_record_tools/requirements-web-monitor-runtime.txt",
    "path": "lib/python/record_tools"
  }
}
```

## 干净容器验收

验证脚本只使用 Python 标准库和包内程序：

```bash
docker run --name apollo-binary-check --network none --entrypoint /bin/bash -d \
  registry.baidubce.com/apollo/apollo-env-gpu:10.0-u22 -c 'exec sleep infinity'
docker cp binary.tar.gz apollo-binary-check:/tmp/binary.tar.gz
docker cp modules/simulation/tools/package/verify_binary.py apollo-binary-check:/tmp/verify_binary.py
docker exec apollo-binary-check bash --noprofile --norc -c \
  'mkdir -p /tmp/package-check && tar -xzf /tmp/binary.tar.gz -C /tmp/package-check && chown -R 1000:1000 /tmp/package-check'
docker exec -u 1000 apollo-binary-check python3 /tmp/verify_binary.py /tmp/package-check/binary
```

验证全量 GPU 包时，为 `docker run` 添加 `--gpus all`，并改为复制及解压
`binary-all.tar.gz`。解压目录需对执行用户可写，以保存日志和仿真结果。

如果包中加入了上例的地图、profile 和场景，可执行实际仿真验收：

```bash
docker exec -u 1000 apollo-binary-check python3 /tmp/verify_binary.py /tmp/package-check/binary \
  --map data/map_data/1haolou_202608241047qh \
  --vehicle-profile profiles/ranger_mini_v3 \
  --scene data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json \
  --ml-scene modules/simulation/ml_planning/examples/1haolou/straight_centered.worldsim.scenario.json
```

检查包括声明的镜像库存在且满足版本要求、包中不存在 `bazel-bin`、运行库目录平铺且没有空目录、全包校验和、包外
链接、每个 ELF 的动态依赖、私有库的同进程加载、Cyber 工具、Web Monitor 的 HTTP/gRPC 和 JS/WASM 资源及选定的
仿真输出通道。Web Monitor 还需通过 `/api/sim` 的 catalog/list；提供地图、车辆
profile 和场景时，提交标准 PNC（Prediction/Planning/Control/Routing，kinematic_control）
和 ML（ML Planning/Routing，perfect_planning）完整 WORLD 任务，每项重复两次，时限 90 秒；
额外 ML 场景用于静态障碍出现后的避障验收。
要求任务 completed，碰撞、连续性、停车目标、车身边界/运动学、重复确定性均 PASS，
ML 另须通过行驶质量、零 estop 和录包真实观测的 C++/Python 网络推理一致性检查。
随后对实际结果录包调用转换、Topic DebugString 和 gRPC 回放窗口 API。
检查实际使用包内程序和库；证据写入包内 `data/binary-verification/`。

标准 PNC 的目标是配置的车头停车点换算后的后轴位置，由冻结车辆、Destination 规则、
flagfile、Routing 和地图独立计算，误差仍要求 0.4 m 内、速度低于 0.05 m/s，
另检查 DEST 停车点一致性及 mission_complete。原始 route endpoint 距离也保留在结果中。
ML 继续按场景请求的后轴目标检查 0.4 m。不会把远处停车当成成功或放宽碰撞/质量阈值。
依赖图语义参见 [Bazel cquery 官方文档](https://bazel.build/query/cquery)。

最新交付 v6：CPU 仿真包 711.8 MiB（147 目标、687 ELF、3556 校验文件）；
全量包 3744.2 MiB（467 目标、9920 ELF、40276 校验文件）。
交付 `/home/wangsheng/workspace/binary-v6.tar.gz` 和 `binary-all-v6.tar.gz`，副本校验通过。
两个无挂载、无网络的干净 Apollo 容器均通过上述完整 WORLD PNC/ML/避障及结果回放流程；
每项两次，共 12 次原生运行，任务分析及推理一致性均通过。
Planning 全程状态正常；Control 初始 100 ms 等待轨迹的消息保留，之后所有帧正常。
ML 使用 perfect_planning，本轮覆盖理想轨迹跟随及避障；传感器设备、其他 GPU 算法、
ML + Control / 动力学和浏览器鼠标操作不在本次验收范围。
29 项打包、29 项任务服务及相关分析回归与四个 C++ 测试目标通过，
旧 pnc_path_test fixture 的独立失败及此前原始仿真失败均保留在证据中。
详见 [WorldSim 完整流程验收](test-artifacts/web-monitor-simulation-20261007/README.md)。
此前 [Viewer v5 验收](test-artifacts/web-monitor-20261007/README.md) 和
[镜像依赖首次验收](test-artifacts/binary-apollo-image-20261007/README.md) 保留为历史记录。
