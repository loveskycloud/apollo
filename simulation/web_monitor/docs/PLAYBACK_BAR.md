# Playback bar

播放条的布局参考来自 `simulation/scene_editor/src/components/playback/PlaybackBar.tsx` 的 `.playback-row`，配色统一使用 web_monitor 的 Carolanne 紫色主题。
Viewer 使用原生 egui 绘制，并未嵌入第二套 HTML 播放器或迁移场景编辑器的示例 KPI 数据。

## 布局和交互

- 主题背景 `#1e1a28`，边框 `#3a3150`，强调紫色 `#9f7aea`，次要文字 `#c4b5fd`；24 px 圆角按钮，6 px 控件间距，左右 12 px 留白。
- 主行顺序：后退、播放/暂停、前进、倍速下拉、时钟下拉、步长下拉、伸缩进度条、`当前进度秒数 / 总秒数 s`、最右侧可编辑时间戳。
- 时间戳使用与下拉框相同的 12 px 比例字体；按 bag 时间范围预留完整数值宽度（最小 112 px），不再固定占用 176 px，播放或编辑不会改变输入框宽度。
- 不提供停止复位、Playback settings、跳到终点或 Seek 提交按钮；暂停保持当前位置。
- 时钟仅为 `publish_time` / `message_time`；步长默认 10 ms，下拉可选 1 / 10 / 100 / 1000 ms，两个下拉框与倍速控件使用相同样式。
- 切换时钟暂停并保留同一个绝对纳秒时间戳，进度位置不变；两个时钟分别查询该时刻的数据，不跳回首帧，也不按已加载窗口截断。重复选择当前时钟不改变播放状态。
- 输入框实时显示所选时钟的原始时间戳，单位为秒、固定九位小数；不是相对 bag 起点的时间。Enter 或编辑后失焦提交，Esc 取消，未修改的失焦不跳转。
- 输入期间冻结草稿，不让播放刷新覆盖；换 bag 或时钟时丢弃旧来源草稿。非法或超出 bag 范围的输入显示红色边框及原因提示，不静默截断到边界；取消后恢复当前时间戳。
- 时间戳格式化和解析只使用整数运算，保留 epoch 级时间戳的纳秒精度，不经 JS/Rust 浮点数往返。
- 网页中紧邻 canvas 的 eframe 透明输入法代理使用 `pointer-events: none`，保留键盘/IME 聚焦，但不截走光标附近的鼠标点击。浏览器命中诊断确认它曾导致再次点击时间框后无法编辑；不修改依赖缓存或增加第二套焦点处理。
- 播放条可用宽度低于 740 px 时采用两行，保证展开侧栏后所有控件仍可访问。
- 进度条亮紫色表示已播放部分、暗紫色表示已入库回执确认的缓存；不以服务器开始请求或发送完成冒充浏览器可播放缓存。
- 点击、拖动、步进和精确跳转仍发送原有 `TimeControlCommand`，主动暂停会取消待恢复播放；范围取固定 bag header，不随当前加载窗口变化。

## 实现和验收入口

源码位于 `re_time_panel/src/time_control_ui.rs` 与 `time_panel.rs`。
`get_playback_state().media_bar` 仅观察实际控件矩形，浏览器测试仍通过真实鼠标和键盘操作。
`scripts/test_scene_playbar_browser.py --out <目录>` 会截取真实 scene editor 播放条作为参考，验收默认步进、无已移除按钮、进度点击/拖动、绝对纳秒时间戳、Enter/失焦提交、非法/越界输入、Esc 取消、播放期间编辑保护、时钟/步长下拉、倍速与窄窗口/侧栏展开后的两行布局。
构建期间可用 `--staging-assets` 预检；正式服务验收必须不带该参数。

`--source-hud` 额外验收主题紫色像素、同一时间戳下的时钟往返、真实底盘速度单位换算，以及 Source 模式下拉、唯一 Open Path 和精简后的 Properties。
2026-09-13 该扩展在正式 9090 验证通过：`test-artifacts/theme-source-hud-20260913/deployed/`，29 组状态/截图，浏览器错误为 0。
相关 4 项 Rust 单元测试（`--all-features`）、Wasm/native Clippy 与部署构建通过；日志保存在同一证据目录。

2026-09-13 最新紧凑右侧时间框正式 9090 验收通过：`test-artifacts/compact-timestamp-20260913/deployed/`，并完成同目录 `topic-regression/` 通道选择回归；浏览器错误均为 0。
所有截图同时断言时间框位于进度文字右侧、宽度小于 176 px 且右对齐（含窄窗口和展开侧栏）。
三项 `re_time_panel` 单元测试（`--all-features`）、Wasm/native Clippy 与完整部署构建通过。
此前含设置菜单的版本证据保留在 `test-artifacts/scene-playbar-20260913/`，不代表当前 UI。
