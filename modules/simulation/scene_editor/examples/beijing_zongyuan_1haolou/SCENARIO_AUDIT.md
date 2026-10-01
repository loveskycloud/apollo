# 北京总院场景合理性审核

保留 396 个场景文件；有效集合 381 个，另 15 个永久阻塞场景标记 INVALID 并移出通行验证集合。清单 excluded 及 evaluation.validity 保存原因、源哈希和几何证据；旧录包和失败结果不变。

有效场景要求真实车身无碰撞、规划轨迹持续有效、终点距离 ≤ 0.4 m。不能因算法失败直接认定场景无效。无效场景保留原 safe_stop 判据便于历史复核，但不计入通过率。局部扫掠审核不等同于任意姿态下的不可通行数学证明。

## 已剔除的永久阻塞场景

| 场景 | 路宽 m | 剩余通道 m | 弯道扫掠与安全余量需求 m |
| --- | ---: | ---: | ---: |
| [coverage_Lane_60_static_left](coverage_Lane_60_static_left.mineproj.json) | 0.755 | 0.646 | 0.737 |
| [coverage_Lane_60_static_right](coverage_Lane_60_static_right.mineproj.json) | 0.755 | 0.644 | 0.737 |
| [coverage_Lane_60_static_slalom](coverage_Lane_60_static_slalom.mineproj.json) | 0.881 | 0.725 | 0.781 |
| [coverage_Lane_60_mixed](coverage_Lane_60_mixed.mineproj.json) | 0.881 | 0.725 | 0.781 |
| [coverage_Lane_60_reverse_static_left](coverage_Lane_60_reverse_static_left.mineproj.json) | 0.757 | 0.610 | 0.738 |
| [coverage_Lane_60_reverse_static_right](coverage_Lane_60_reverse_static_right.mineproj.json) | 0.753 | 0.651 | 0.692 |
| [coverage_Lane_60_reverse_static_slalom](coverage_Lane_60_reverse_static_slalom.mineproj.json) | 0.856 | 0.686 | 0.790 |
| [coverage_Lane_60_reverse_mixed](coverage_Lane_60_reverse_mixed.mineproj.json) | 0.856 | 0.686 | 0.790 |
| [coverage_Lane_65_static_left](coverage_Lane_65_static_left.mineproj.json) | 0.887 | 0.688 | 0.777 |
| [coverage_Lane_65_reverse_static_right](coverage_Lane_65_reverse_static_right.mineproj.json) | 0.925 | 0.772 | 0.788 |
| [coverage_Lane_67_static_left](coverage_Lane_67_static_left.mineproj.json) | 0.831 | 0.705 | 0.834 |
| [coverage_Lane_67_static_right](coverage_Lane_67_static_right.mineproj.json) | 0.831 | 0.745 | 0.834 |
| [coverage_Lane_67_reverse_static_left](coverage_Lane_67_reverse_static_left.mineproj.json) | 0.818 | 0.688 | 0.785 |
| [coverage_Lane_67_reverse_static_right](coverage_Lane_67_reverse_static_right.mineproj.json) | 0.818 | 0.658 | 0.785 |
| [coverage_Lane_67_reverse_static_slalom](coverage_Lane_67_reverse_static_slalom.mineproj.json) | 0.721 | 0.625 | 0.640 |

## 场景生成修正与审核边界

- 使用原始地图车道宽度采样，并估算 Ranger 车头弯道扫掠；不只比较车宽。
- 演员出生点与触发区内主车路线保持至少 3 米距离；横穿终点距主车路线至少 1.2 米。
- 永久阻塞的混合场景中，脚本行人避开合理等待区，避免演员强行走入正确停车的主车。
- 急弯横穿路线额外避开上游让行区域；优先保留原路线，仅在有交叉冲突时缩短人行道路径或调整横穿位置，出生 3 米及终点 1.2 米的距离要求保持不变。
- 连续小车改为同一通道、同方向、同速度且间距 1.4 米的连续流，避免在急弯上强加互相交叉的脚本路线。
- 扩展上游路线，尽量保留 7 米起步引导距离，避免路线截断导致车辆直接从狭窄急弯中起步。
- 原生物理碰撞检测不变；旧失败录包和输入快照保留。
- 这是几何及预期审核，不是全套通过证明；完整连续车身轨迹和动态交互仍需仿真与回放。

每个 `.evaluation.json` 包含预期、理由、空间计算、停止区域和源文件 SHA-256。编辑场景后必须重新审核，哈希不符会拒绝沿用过期判据。
