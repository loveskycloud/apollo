# 自动驾驶算法工作台

Layout 是窗口布局，Panel 是其中一个停靠窗口。
窗口和数据源分离：左上角 Layers 是分层显示开关，同时请求当前时间的数据；取消勾选立即隐藏已缓存实体，不删除缓存。
算法检查窗口按需直接查询 MCAP，不依赖 3D 通道是否勾选。

## 显示图层

- `sensing/camera/camera_front/front120`、`front30`：两个独立前视镜头；`camera_left/right/back` 按 bag 实际相机映射，不伪造缺失设备。
- `sensing/lidar/main` → `/lidar/up/points`；若存在侧雷达语义数据，归到 `side_left/right`。
- `localization/pose`：主车 GLB；`planning/trajectory`：当前规划线。
- `perception/obstacles`：感知障碍物线框与 ID/类型；`prediction/trajectories`：每个障碍物的各条预测假设，保留概率标签。
- 父级复选框批量控制子图层，部分选中显示中间态；鼠标悬停查看实际传输 topic。
- 默认相机、主车开启；点云、规划、感知、预测关闭，按需勾选。全局图层开关适用于当前 Layout 的所有 3D/图像 Panel；独立算法调试 Panel 仍按自身配置查询。
- 加载依赖与显示权限独立：隐藏主车不会阻断点云/轨迹所需的坐标变换；原始 `/apollo/planning` 不再作为第二个轨迹开关。
- 点云默认按固定传感器 Z 高度着色：≤0 m 黄色，2 m 橙色，5 m 粉色，≥10 m 紫色，中间连续过渡；不是每帧 min/max 归一化。已有显式点颜色/覆盖配置保留。

当前 control bag 的 597 条 prediction 消息没有 trajectory_point，因此预测开关选中后显示“当前无几何数据”，不生成假轨迹。

## 3D 仪表盘与相机

每个 3D Panel 的底部居中叠加 Dashboard，可用标题上的 − / + 收起、展开。
收起状态按 View 独立记忆；不占用其它 Panel，也不改变播放状态。
右侧立方体按钮切换自由 3D / 锁定俯视，准星按钮开启或关闭持续跟随主车。
俯视锁定水平面：未跟随时左/右/中键拖动只平移 XY，滚轮缩放；修饰键、双击、游戏手柄和键盘不能绕过锁定转回轨道 3D。
俯视保留垂直向下的透视投影，并非正交投影；“平面模式”描述的是交互约束。
跟随开启时，以当前 pose 的真实 position 和 +Y 前向的水平投影（heading）逐帧更新，不继承任意自由相机朝向，也不跟随车身 roll/pitch 颠簸。
3D 默认距离主车 50 m：后方 40 m、上方 30 m，约 37° 斜向下看向主车；俯视默认高度 70 m，车头始终朝屏幕上方。
两种模式分别保留缩放值，范围为 20–250 m；跟随期间只允许缩放，拖动不会解除跟随或改变固定相对朝向。
再次点击准星关闭跟随，相机保持当前位置；3D 恢复自由轨道操作，俯视恢复平面平移，后续播放/seek 不再移动自由相机。
切换俯视/3D 保留跟随开关；重新开启跟随恢复舒适默认距离。
跟随使用当前空间 View 的真实坐标变换（含 instance pose），pose/heading/TF 不可用时不能开启；已开启后暂时缺失则停在最后有效视角并明确显示 Follow paused，仍可点击取消，不定位到伪造的原点。
这些模式为各 3D View 的运行时状态，不逐帧写 blueprint，不制造 undo 项或带入相机插值滞后；暂停跳转直接对齐所显示的当前 pose。
侧边按钮参考 Dreamview Plus `VehicleViz/ViewBtn` 与 `useStyle.ts`：32×32、6 px 圆角、16 px 图标、无描边，深灰蓝背景 #343C4D，默认图标 #96A5C1，激活蓝色 #3388FA。
采用视角立方体和定位准星矢量图标，去除 Top / Ego 常驻文字；悬停显示操作说明，禁用时灰显并提示缺少 pose/TF。
提示在按钮左侧展开，避免覆盖下方按钮；点击恢复后不再保留俯视激活蓝色。
放在 3D 内右侧 24 px、顶部 25 px，避开底部仪表盘。

仪表盘的车速、带符号转向百分比、驾驶模式、挡位、油门、刹车全部来自 `/apollo/canbus/chassis`。
点击 SPEED 区域（标题、数值或单位）切换 km/h / m/s，默认 km/h；底盘原始 speed_mps 不变，仅显示时乘 3.6，单位偏好由各个 3D Panel 共享并持久保存。
切换单位不跳转时间、不改变播放状态，也不从 control 补缺失值。
与 Dreamview 的 `SimulationWorldService::UpdateSimulationWorld(const Chassis&)` 取值链一致；不从 `/apollo/control` 替代反馈。
字段未提供或非有限值显示 **—**，真实零显示 **0.0**；与 Dreamview 前端缺失时回零不同，这里保留缺失以便排查。
转向 + 表示逆时针/左，− 表示顺时针/右，单位为百分比，不能当作角度。
AUTO、MANUAL、AUTO STEER、AUTO SPEED、EMERGENCY 分别显示；标准 Apollo Chassis 不含 REMOTE 枚举，只有来源显式提供 REMOTE 时才能显示，不从 emergency 或手动状态猜测遥控。
交通灯来自 `/apollo/perception/traffic_light`，保留多灯 ID、颜色与置信度供悬停查看，不猜测哪一个是本车路线的信号灯。
无 topic、无消息、空检测、未知颜色、过期数据分别提示；交通灯超过 1 秒变灰标 STALE，底盘超过 500 ms 标 STALE 并显示年龄。
控制指令仍可在独立的 Control 调试窗口查看，不混进仪表盘。

数据查询使用共享的 `dashboard_window`：前 1 秒、后 5 秒，加上左边界之前的最近消息；每次绘制按当前游标 latest-at 选择，不选未来样本。
剩余不足 2 秒时后台预取，每个 source/clock 只保留一个有界共享窗口，所有 3D Panel 复用；窗口内播放没有查询加载圈。
跳转到窗口外时显示等待当前数据，不把旧时间的数值冒充当前值；查询错误显式显示。

20260729 control bag 的 5997 条 chassis 消息均有车速、转向和 AUTO 模式，但油门/刹车反馈字段全部缺失，因此显示 —；该 bag 也没有交通灯 topic。
这不是仪表盘使用控制指令进行补齐的理由。

## 默认布局

| Layout | Panel | 目的 |
| --- | --- | --- |
| Planning（4 个） | Planning 3D | 点云、主车姿态、当前规划曲线空间叠加 |
| | Trajectory XY | 当前规划与过去 10 秒实际定位轨迹对比 |
| | Planning profile | 当前规划 v / a / kappa 对 relative_time，分别使用 m/s、m/s²、1/m |
| | Planning message | 原始规划消息、状态/决策字段、前后消息、固定字段 |
| Control（6 个） | Control 3D | 环境、主车和参考路径 |
| | Speed tracking | control 参考速度与 chassis 实测速度 |
| | Steering feedback | 转向指令与反馈（百分比，不冒充方向盘角或前轮角） |
| | Tracking errors | station_error 与 lateral_error（米） |
| | Pedals | control 的 throttle / brake 指令（百分比，与 3D 仪表盘底盘反馈区分） |
| | State transitions | 自动驾驶模式、挡位、驻车状态 |

Control 可从 Add 菜单添加加速度跟踪、航向误差（rad）、控制器耗时（ms）以及完整控制看板。
规划/控制默认不放 Front120、Front30：没有相机的 bag 不应被黑色相机窗口占据主要工作区。
有相机的 bag 会在 Add 菜单列出对应相机窗口，感知布局保留四路相机。
其它可选工具有 Value watch、Signal plot、Topic health、任意 topic 的 Inspector。
当前未实现独立 ST/SL 障碍物决策图、HD map 解码或闭环仿真，不以空窗口冒充已实现功能。

## 自定义布局

1. 打开左侧 Panel，直接添加调试窗口，或在 Add panel / split / tabs 菜单中添加 3D、bag 内相机、其它视图/容器。
2. 拖动窗口标题到目标边缘可分割，到标题区域可组成标签页；拖动分隔线调整大小。
3. 左侧 Current panels 的 × 删除指定窗口。
4. Save as Custom layout 保存为本浏览器的 Custom 槽位，并设为默认布局。
5. Export layout (.rbl) 后，在保存对话框点击下载链接，保存可移植备份。

预设布局可随时重新打开；修改后请先 Save as Custom，再切换到别的预设。
Custom 当前只有一个保存槽位，保存会替换该槽位；需要多份版本时导出 .rbl。
保存内容包含窗口树和工具配置，不包含 bag 数据。
刷新后重新打开数据源；布局和工具配置会保留。
切换 Layout 不应改变播放时间、时间轴类型或暂停状态。
Web 查询工具不发布控制指令，不连接车辆执行器。

## 3D 数据和坐标

转换器版本为 semantic-mcap-v12，旧 record 的转换缓存会自动失效并重新生成；旧 MCAP 可读，但新增感知/预测图层需重开原始 record。

- `/vehicle`：定位 PoseInFrame，GLB 与当前 latest-at 的平移/四元数共同渲染。
- `/planning/trajectory`：从原始 `/apollo/planning` 生成的 LineStrips3D；空轨迹显式清除上一条曲线。
- `/perception/obstacles`、`/prediction/trajectories`：共享多折线语义协议，保留各框/假设边界；每条完整消息覆盖前一帧，空消息显式清空。
- `/lidar/up/points`：保留雷达采样时间，使用该时间之前的定位和 bag 中实际静态外参定位点云。
- 统一使用 `wm_map_local`；先在 float64 中减去第一条定位的位置，再转换 GPU float32，避免 900 万米坐标损失亚米精度。
- Apollo 定位 +Y 向前、+X 向右、+Z 向上；GLB 使用相同方向。
- 规划未给 z 时仅表示 XY 曲线，显示平面取首次定位高程，不宣称包含真实道路高程。

GLB 是项目自制 1.2 × 0.8 米主车示意符号，来自 `scripts/generate_ego_glb.py`。
它不是 Ranger 实车 CAD 或碰撞包络；精确尺寸/轴距/安装位置需要用户提供对应车辆模型与配置。

含 control 的 `20260729201441.record.00000.20260729201441` 具有 `localization → imu → rslidar_up` 外参链。
旧 `20260514114049.record.00000.20260514114049` 缺少 rslidar_up 的外参链，不能借用其 velodyne 外参。
这类数据明确提示缺失 TF，并将点云放在隔离传感器帧中显示，禁止与主车/规划错误叠加。
完善标定后重新转换才能验证空间对齐。

## 验收

仪表盘脚本 `scripts/test_vehicle_dashboard_browser.py`：真实 Chromium 鼠标展开/收起、底部居中几何检查、跨布局、三次暂停 seek 与原始 chassis exact latest-at 比对、Ego 定位/Top 俯视/恢复朝向，以及 350 ms 查询延迟下缓冲播放。
`test-artifacts/dashboard-20260912/run1/` 首轮通过，播放采样 28 个不同底盘样本，无 panic/pageerror。
`run3/` 为最终构建的完整复测：点云与规划同时开启，补充仪表盘拖动不影响相机、窄窗口自适应、Top 按钮显示检查，28 个不同底盘样本均与原始消息一致。
`deployed/` 在正式 9090 重启后使用用户原始 `/home/wangsheng/code/apollo/data/bag/20260729201441.record.00000.20260729201441` 路径复测同一完整流程通过，含 host/container 路径映射和 bare URL 自动连接；Wasm/native Clippy 与完整构建通过。
Dreamview Plus 图标按钮版：`test-artifacts/camera-icons-20260912/run2/` 回归通过，额外通过截图像素检查确认提示位于左侧而不覆盖按钮；默认、悬停、激活状态均人工查看截图确认。
`camera-icons-20260912/deployed/` 在正式 9090 使用原始 record 路径复测通过：相机操作、三次暂停跳转、窄窗口、收起/展开、350 ms 延迟注入下 28 个底盘样本更新；Wasm/native Clippy 和完整构建通过。

持续跟随与俯视锁定版：`scripts/test_vehicle_dashboard_browser.py --camera-modes`，`test-artifacts/camera-follow-20260912/run1/` 通过。对照 MCAP 原始 pose 的平移/四元数核验当前相机，而非仅检查按钮状态；覆盖三键拖动、Ctrl/Alt 拖动、双击、平面缩放、暂停跳转、两种视角各 16 个播放采样、取消跟随后自由视角保持与恢复轨道操作。Wasm/native Clippy 与完整构建通过。
`camera-follow-20260912/deployed/` 为正式 9090 重启后、从原始 `20260729201441.record.00000.20260729201441` 路径打开的同套完整复测，包含最终 28 个缓冲底盘样本，浏览器无未捕获错误。使用软件 WebGL，功能验收不等同于硬件高帧率性能验收。
`test_dashboard_query.py` 覆盖缺失与真实零的区别、禁止 control 混入、显式时钟、窗口前驱消息、多灯/空灯/缺失颜色和预算错误；与已有回归合计 24 项通过。

2026-09-12 Layers 最终验收：`test-artifacts/layers-20260912/deployed/` 使用 9090 与用户原始 control record，验证规划默认关闭、主车/规划/点云取消后隐藏但缓存仍在、重新勾选、跨 Planning/Control 布局保持状态、暂停 seek，以及黄色/橙色点云像素实际渲染。
`cameras2/` 验证真实七路相机映射、Front120/Front30 独立、缓存图像隐藏/恢复、父级 camera 批量关闭。
`paused-regression/` 验证旧 bag 暂停首帧、三次精确 latest-at 跳转、缓存内播放 16 帧点云；无自动播放或浏览器 panic。
20 项 Python 测试、2 项 Rust catalog 测试、Wasm/native Clippy 和完整构建通过。
control 的 v12 转换缓存已通过正式转换 API 生成。

`scripts/test_layouts_browser.py` 使用真实 Chromium 鼠标、键盘、截图和只读诊断接口。
检查三套布局、暂停跳转、MCAP exact latest-at、窗口增删、Custom 恢复、.rbl 下载与刷新持久化。
测试产物位于 `test-artifacts/layouts-20260912/`。
`modules/simulation/tools/apollo_record_tools/test_ad_scene.py` 覆盖静态外参组合、不使用未来定位、缺失 TF 不当作 identity，以及大坐标重定位精度。

2026-09-12 旧相机 bag 回归：`lidar-regression/` 验证勾选后暂停首帧、三次暂停跳转、缓存内播放（17 个不同点云样本，无缓冲暂停）。
该测试采用软件 WebGL，不代表硬件实时帧率或相机逐帧性能验收。
Python 场景/调试查询/窗口规划共 18 项单元测试通过。

最终完整验收 `run7/` 通过：使用原始 control record 经 `/api/convert_record` 生成的 v11 缓存；三套布局暂停显示点云、主车、规划线，切换布局保持精确游标；两次不同时间的点云样本、主车平移和规划点数/首尾坐标与 MCAP exact latest-at 一致。
Control 四组曲线均检查非空且无字段错误；真实鼠标验证增删窗口、拖拽停靠、Custom 切换恢复、.rbl 下载、刷新及重开 bag 后保留自定义窗口组合。
Wasm/native Clippy、完整 Viewer 构建通过；浏览器无 panic/pageerror。
9090 部署后 `deployed/` 再以用户原始 `/home/wangsheng/code/apollo/data/bag/20260729201441.record.00000.20260729201441` 路径打开（验证容器路径映射与 v11 缓存），未加载点云、未播放时 GLB 和当前规划曲线可见。
