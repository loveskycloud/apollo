# Rerun Data Conversion Guide

本文档梳理如何把不同来源的数据转换为 Rerun 可视化格式，以及 Apollo CyberRT record 这类专有时序数据最合适的处理方式。

## 1. Rerun 的核心数据模型

Rerun 不是传统视频播放器，也不是只读某一种 bag 格式的工具。它的核心模型是：

- **Recording**：一次记录，可保存为 `.rrd`。
- **Timeline**：时间轴，例如 `record_time`、`publish_time`、`message_time`、`frame_index`。
- **Entity path**：实体层级，例如 `vehicle`、`vehicle/lidar/points`、`camera/front`、`hdmap/lane_center`。
- **Archetype**：具体可视化对象，例如 `Points3D`、`LineStrips3D`、`Image`、`AssetVideo`、`VideoFrameReference`、`Transform3D`。

转换数据到 Rerun 的本质是：把原始数据解码成 Rerun archetype，并给每条数据打上正确 timeline 和 entity path。

## 2. Rerun 原生支持的文件格式

Rerun Viewer/SDK 有内置 data loader，可以直接打开部分文件：

- **Rerun 原生文件**：`.rrd`
- **MCAP**：`.mcap`
- **3D 模型**：`.gltf`、`.glb`、`.obj`、`.stl`
- **点云**：`.ply`
- **图片**：`.png`、`.jpg`、`.jpeg`、`.webp`、`.bmp`、`.tiff`、`.exr`、`.hdr`、`.gif` 等常见图片格式
- **文本**：`.md`、`.txt`
- **LeRobot 数据集**：目录形式的数据集

MCAP 是 Rerun 当前最重要的通用时序容器之一。Rerun 可以直接打开 MCAP，也可以把 MCAP 转成 `.rrd` 以获得更快加载：

```bash
rerun data.mcap
rerun mcap convert data.mcap -o data.rrd
rerun data.rrd
```

需要注意：

- `.rrd` 是 Rerun 原生格式，加载最快，最适合最终可视化交付。
- `.mcap` 更适合作为跨工具交换格式，尤其是 ROS 2 / Foxglove 生态。
- Rerun 对常见 ROS 2 / Foxglove 消息有语义转换；对其他 protobuf 消息更多是反射解码，不一定能自动变成 3D 点云、轨迹或图像。
- Rerun 支持自定义 data loader，因此专有格式不一定要改 Rerun 源码，可以做外部转换器。

## 3. 常见数据到 Rerun 的转换路线

### 3.1 图片

直接用 SDK 记录为 `rr.Image`：

```python
import rerun as rr
from PIL import Image
import numpy as np

rr.init("image_viewer")
rr.save("images.rrd")
rr.set_time("record_time", duration=0.0)
image = np.asarray(Image.open("front.png").convert("RGB"))
rr.log("camera/front", rr.Image(image))
```

适合离散图片、低频图像或调试样例。

### 3.2 视频

推荐方式是保留视频容器，记录 `AssetVideo` 和 `VideoFrameReference`，不要把视频拆成大量 PNG：

```python
import rerun as rr

video = rr.AssetVideo(path="front.mp4")
rr.log("camera/front/video_asset", video, static=True)

for frame_ns in video.read_frame_timestamps_nanos():
    rr.set_time("record_time", duration=frame_ns / 1e9)
    rr.log(
        "camera/front",
        rr.VideoFrameReference(
            nanoseconds=frame_ns,
            video_reference="camera/front/video_asset",
        ),
    )
```

工程建议：

- 原始 H264/H265 裸流先封装或转码为 MP4。
- 为了随机拖动稳定，H264 建议使用较密关键帧、无 B 帧、`yuv420p`、`faststart`。
- 如果浏览器 WebCodecs seek 报 `failed to decode chunk: DataError`，通常是视频缺少关键帧/SPS/PPS 或编码参数不适合随机访问。

### 3.3 点云

点云通常记录为 `rr.Points3D`：

```python
import rerun as rr
import numpy as np

points = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], dtype=np.float32)
colors = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255]], dtype=np.uint8)

rr.set_time("record_time", duration=1.0)
rr.log("lidar/points", rr.Points3D(points, colors=colors, radii=0.03))
```

点云坐标系要先确定：

- 如果点云已经是全局地图坐标，放在根坐标系，例如 `lidar/map_points`。
- 如果点云是车体/雷达局部坐标，应挂在 `vehicle/lidar/points`，并让它继承 `vehicle` 的 `Transform3D`。
- 不要把局部点云直接当地图坐标画，否则会和 pose、HDMap 错位。

### 3.4 轨迹和位姿

位姿一般用 `rr.Transform3D`，轨迹用 `rr.LineStrips3D`：

```python
rr.set_time("record_time", duration=t)
rr.log(
    "vehicle",
    rr.Transform3D(
        translation=[x, y, z],
        quaternion=[qx, qy, qz, qw],
    ),
)
rr.log("trajectory", rr.LineStrips3D([trajectory_points]))
```

推荐层级：

- `vehicle`：只记录车辆当前 transform。
- `vehicle/position`：车辆局部原点 marker，坐标为 `[0, 0, 0]`。
- `trajectory`：根坐标系下的全局轨迹，不要放在 `vehicle/trajectory`，否则会继承车辆 transform 造成双重变换。

### 3.5 HDMap / 车道线 / 道路边界

HDMap 通常转成静态 `LineStrips3D`：

- `hdmap/lane_center`
- `hdmap/lane_left_boundary`
- `hdmap/lane_right_boundary`
- `hdmap/road_boundary`
- `hdmap/stop_line`
- `hdmap/crosswalk`

Apollo HDMap 的 `base_map.bin` / `base_map.txt` 可以解析为 `apollo.hdmap.Map`，再把 lane boundary、road boundary、stop line 等曲线转为 Rerun 线段。

如果地图是 2D flat map，常见情况是 HDMap z 全为 0，而 localization z 是绝对海拔。此时不要把地图 z 减掉 localization altitude，否则地图会被错误放到地下几十米。

### 3.6 ROS / ROS 2 数据

推荐路线：

1. ROS/ROS 2 bag 转 MCAP。
2. 用 Rerun 直接打开 MCAP，或转成 RRD。

```bash
rerun data.mcap
rerun mcap convert data.mcap -o data.rrd
```

如果消息类型是常见 ROS 2 sensor message，Rerun 可能可以自动转换成图像、点云、TF 等。如果是自定义消息，建议写 Python/Rust 转换器，显式映射成 Rerun archetype。

## 4. Apollo CyberRT Record 的推荐处理方式

Apollo `.record` 不是 MCAP，也不是 ROS bag。它是 CyberRT record，包含：

- channel name
- message timestamp，即 publish time / record time
- protobuf type name
- protobuf descriptor
- protobuf payload

Rerun 不原生读取 Apollo record。因此最合适的路线是：

```text
Apollo .record
  -> C++ CyberRT RecordReader
  -> JSONL bridge / slice / index
  -> Python semantic decoder
  -> Rerun .rrd
```

本仓库已有工具：

- `apollo_record_tool.cc`：C++ 读取 record，负责 index、split、slice、dump-jsonl。
- `apollo_record_to_mcap.py`：把 Apollo record 转成 MCAP，保留 Apollo protobuf payload 和 descriptor。
- `apollo_record_to_rerun.py`：把 Apollo record 直接转成 Rerun `.rrd`，负责语义可视化。

### 4.1 为什么不直接把 Apollo record 转 MCAP 后交给 Rerun

这条路线可以做，但不是最佳可视化路线：

```text
Apollo record -> MCAP -> Rerun
```

优点：

- MCAP 有索引，适合随机读取。
- 容器通用，方便和 Foxglove / ROS 工具链交换。
- 可以保留 Apollo 原始 protobuf 和 descriptor。

缺点：

- Apollo protobuf 不是标准 ROS 2 sensor message。
- Rerun 看到 Apollo 自定义 protobuf 时，不一定知道哪个字段是点云、图像、pose、HDMap。
- 结果可能只是“能解码字段”，但不能自动形成理想的 3D 场景。

因此，MCAP 更适合做中间交换格式；如果目标是高质量可视化，最好直接把 Apollo 语义映射到 Rerun archetype。

### 4.2 推荐工作流

先建立索引：

```bash
cd /apollo_workspace/modules/simulation/tools/apollo_record_tools

bin/apollo_record_tool index \
  -o /apollo_workspace/data/bag/index.jsonl \
  /apollo_workspace/data/bag/*.record.*
```

查看 channel、时间范围和消息数量：

```bash
python3 - <<'PY'
import json, collections
counts = collections.Counter()
types = {}
for line in open("/apollo_workspace/data/bag/index.jsonl"):
    row = json.loads(line)
    ch = row["channel"]
    counts[ch] += 1
    types.setdefault(ch, row.get("type", ""))
for ch, n in counts.most_common():
    print(f"{n:8d} {ch} {types[ch]}")
PY
```

切片调试：

```bash
bin/apollo_record_tool slice \
  --begin-ns 1726210734079215283 \
  --duration-ms 5000 \
  -o /apollo_workspace/data/bag/slice.record \
  -c /apollo/localization/pose \
  -c /apollo/sensor/rslidar/up/PointCloud2 \
  /apollo_workspace/data/bag/*.record.*
```

导出 Rerun：

```bash
PYTHONPATH=/opt/apollo/neo/python \
PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python \
python3 apollo_record_to_rerun.py \
  -o /apollo_workspace/data/bag/apollo_view.rrd \
  --time-mode message \
  --color-mode semantic-lite \
  --max-points 120000 \
  --camera FrontLeft \
  --camera Front120 \
  --camera FrontRight \
  /apollo_workspace/data/bag/*.record.*
```

带 HDMap：

```bash
PYTHONPATH=/opt/apollo/neo/python \
PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python \
python3 apollo_record_to_rerun.py \
  -o /apollo_workspace/data/bag/apollo_hdmap_view.rrd \
  --time-mode message \
  --hdmap /apollo_workspace/data/bag/data_with_map/extracted/od_hq_map/base_map.bin \
  --color-mode semantic-lite \
  --max-points 120000 \
  /apollo_workspace/data/bag/data_with_map/extracted/*.record.*
```

打开：

```bash
python3 -m rerun /apollo_workspace/data/bag/apollo_hdmap_view.rrd
```

容器里图形能力不足时，用 web viewer：

```bash
python3 -m rerun /apollo_workspace/data/bag/apollo_hdmap_view.rrd \
  --web-viewer \
  --web-viewer-port 9090 \
  --bind 0.0.0.0
```

然后打开 `http://127.0.0.1:9090`。

### 4.3 时间同步策略

Apollo record 至少有两类时间：

- **publish time**：record message timestamp，消息被发布/记录的时间。
- **message time**：protobuf 内部字段，例如 `measurement_time`，传感器采集或估计时间。

推荐在 Rerun 里同时写入：

- `publish_time`
- `message_time`
- `record_time`
- `record_index`

默认可视化建议使用 `message_time`，因为多传感器同步更应该对齐采集时间，而不是写入 record 的时间。

### 4.4 坐标系策略

Apollo record 可视化里最容易出错的是坐标系。

推荐约定：

- 根坐标系：地图 ENU / UTM 局部坐标。
- `hdmap/*`：根坐标系静态线段。
- `trajectory`：根坐标系全局轨迹。
- `vehicle`：车辆在根坐标系下的 `Transform3D`。
- `vehicle/lidar/*`：雷达局部点云，继承 vehicle transform。
- `camera/*`：如果只做图像面板，可以独立显示；如果要 3D 标定投影，应挂到 vehicle/camera 外参下面。

典型错误：

- 把局部点云直接画在根坐标系，导致点云不跟 vehicle position 对齐。
- 把 `trajectory` 放在 `vehicle/trajectory` 下，导致轨迹继承车辆 transform，产生双重变换。
- HDMap z=0 时仍减掉 localization 的绝对 altitude，导致地图和点云高度差几十米。

### 4.5 视频策略

Apollo camera record 常见是压缩图像或 H264/H265 payload。推荐：

1. 从 protobuf payload 中抽取 H264/H265。
2. 封装或转码成 MP4。
3. 用 `AssetVideo` + `VideoFrameReference` 记录到 Rerun。

不推荐默认拆 PNG：

- `.rrd` 会变得非常大。
- 生成慢。
- 随机播放体验差。

如果随机拖动报 `failed to decode chunk: DataError`，优先转码成 seek-friendly MP4：

```bash
ffmpeg -y \
  -r 25 \
  -i raw.h264 \
  -an \
  -c:v libx264 \
  -preset veryfast \
  -crf 23 \
  -profile:v baseline \
  -pix_fmt yuv420p \
  -bf 0 \
  -g 10 \
  -keyint_min 10 \
  -sc_threshold 0 \
  -x264-params repeat-headers=1 \
  -movflags +faststart \
  camera.mp4
```

### 4.6 点云上色策略

可选策略：

- `intensity`：按反射强度灰度显示。
- `height`：按高度着色。
- `range`：按距离着色。
- `semantic-lite`：启发式显示，地面黄/棕、低障碍橙、植被类绿色、高结构蓝、强反射青白。

`semantic-lite` 不是语义分割，只是工程调试中更容易读图的启发式配色。

## 5. 什么时候输出 MCAP，什么时候输出 RRD

推荐原则：

- 要跨工具交换、长期保存原始消息：输出 MCAP。
- 要高质量 Rerun 可视化、随机拖动、快速打开：输出 RRD。
- 要调试一小段数据：先 slice record，再转 RRD。
- 要做大规模数据服务：先 index/split，再按时间窗口按需转换。

对 Apollo record，最实用的组合是：

```text
原始 record 作为真源数据保留
index.jsonl 用于时间和 channel 查询
小窗口 slice.record 用于调试
MCAP 用于跨工具交换
RRD 用于最终可视化
```

## 6. 推荐的 Apollo Record 架构

长期看，推荐做成三层：

### 6.1 Record Access Layer

职责：

- 读取 CyberRT record。
- 建索引。
- 按时间切片。
- 按 channel 过滤。
- 输出 schema + payload。

实现建议：C++，直接使用 Apollo `RecordReader` / `RecordWriter`。

### 6.2 Semantic Decode Layer

职责：

- 把 Apollo protobuf 转成业务语义。
- 识别 localization、lidar、camera、HDMap、planning、prediction、perception。
- 处理坐标系、时间同步、采样和降采样。

实现建议：Python 优先，便于快速接入 Rerun SDK；性能瓶颈再下沉 C++。

### 6.3 Visualization Export Layer

职责：

- 写 `.rrd`。
- 写多 timeline。
- 组织 entity hierarchy。
- 输出 viewer-friendly 的视频、点云和地图。

实现建议：

- `message_time` 作为默认 `record_time`。
- `publish_time` 作为备用 timeline。
- 大视频用 `AssetVideo`。
- 大点云按帧降采样或分块。
- 局部传感器数据挂在 `vehicle/*` 下。

## 7. Checklist

转换 Apollo record 到 Rerun 前，先检查：

- record 里有哪些 channel？
- 点云是全局坐标还是车体/雷达局部坐标？
- camera 是否真的在 record 里，还是在另一个包里？
- protobuf 内是否有 `measurement_time`？
- HDMap 是 2D z=0 还是带真实 z？
- localization 的 quaternion 顺序是否为 `[qx, qy, qz, qw]`？
- Rerun entity 是否会因为父 transform 造成双重变换？
- 视频是否适合随机 seek？

只要这些问题处理好，Apollo record 到 Rerun 的体验会比直接 play topic 更适合随机访问、快进、慢放和多传感器同步分析。
