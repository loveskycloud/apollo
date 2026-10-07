# Web Monitor 二进制分发修复（2026-10-07）

问题：全量包收集了 C++ 启动器，却没有收集它用 exec 启动的 Rust Viewer，
也遗漏了三个布局。启动器退到 PATH 查找，最终显示 rerun: not found。
Bazel 5 的 cquery files 输出还会省略源文件布局，导致只补 genrule 仍不完整。

修复：在原启动器中优先按自身路径解析 Viewer 和布局，使用 exec 传递参数，
依赖不存在时直接报错。类型配置通过 runtime_dependencies 指定额外目标，
Starlark cquery 收集生成文件与源文件。Viewer 放在顶层 bin/rerun，布局在
modules/simulation/web_monitor/layouts；两者经过同样的打包及校验流程。
不安装官方 pip Rerun，因为项目的 fork 才包含仿真与算法调试界面。

## 验收

- 27 项打包回归通过，包含外部 Viewer 及源布局收集。
- 3 项启动器回归通过：其他工作目录、空格/引号/字面参数、显式缺失依赖报错及退出码。
- 独立 Viewer 包和最终全量包分别在无挂载、无网络的 Apollo 容器中由 UID 1000 验收。
- 最终全量包：3713.0 MiB，466 目标，9887 ELF，38968 校验文件，928 包内运行库，127 镜像库。
- 校验、依赖、Cyber C++/Python、16 个私有库同进程加载及 3 秒 PNC 仿真通过；1030 条消息、10 个通道。
- 从 /tmp 启动包内 web_monitor_main，HTTP 9090 / gRPC 9876 正常，HTML、JS、56 MB WASM 和图标均返回实际资源；与独立包内容哈希一致。
- 未修改解压后的程序或库来修补验收。源码、缓存和依赖卷未挂载。

镜像：registry.baidubce.com/apollo/apollo-env-gpu:10.0-u22。
ID：sha256:7c33b27bf30bccf5c13f4d1058c72437930681b646ee0b7bfece979f482a2530。
manifest 格式仍为 4，交付修订命名 v5。副本与源压缩包的 SHA-256 均已验证。
OpenSSL 继续使用镜像内 Apollo SDK 配对，系统库继续按清单复用。

本次 Viewer 检查验证启动、端口和资源服务，未重新逐项验收 UI 交互或录包转换。
无 HOME 的隔离测试环境产生 analytics 配置初始化提示，日志原样保留；没有以此代替 Viewer 运行检查。

## 使用

挂载 /home/wangsheng/workspace 到容器 /apollo_workspace 后：

```bash
cd /apollo_workspace
mkdir -p package-v5
tar -xzf binary-all-v5.tar.gz -C package-v5
source package-v5/binary/setup.bash
web_monitor_main
```

重建命令与此前全量包一致：gen_binary.py --type all --output binary-all.tar.gz，
本次保留了地图、车辆 profile 和 WorldSim 场景的三个 include，用于 PNC 验收。

```bash
docker exec -u 1000 apollo-binary-web-monitor-20261007 \
  python3 /tmp/verify_binary.py '/tmp/final web monitor all v5/binary' \
  --map data/map_data/1haolou_202608241047qh \
  --vehicle-profile profiles/ranger_mini_v3 \
  --scene data/scenarios/ranger_mini_v3_1haolou_smoke.scenario.json
```

完整数据见 [summary.json](summary.json)；all 中保存 manifest / ldd 压缩日志、
result、Viewer 端口/资源哈希、原启动日志、容器条件、目标列表及 archive SHA-256。
viewer-only 中保存首次独立运行包的对应启动证据。

可复用经验：ELF 依赖闭包覆盖不了子进程程序和数据文件，应把它们作为明确的
运行依赖纳入构建、收集和启动验收；只运行 ldd 或打印浏览器网址不能证明 Viewer 可用。
这类经验适合归档到 Obsidian 的 50-Solutions。
