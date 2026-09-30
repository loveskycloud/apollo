# scene_editor（场景编辑器）

即原 `scenario-editor` Web 工程，现位于：

`/apollo_workspace/modules/simulation/scene_editor/`（容器内）

编辑器保存可继续编辑的项目，也可单独导出供统一仿真器加载的 WorldSim Scenario proto JSON。

## 启动

```bash
source ~/.nvm/nvm.sh && nvm use --lts
cd /apollo_workspace/modules/simulation/scene_editor
npm install   # 首次
npm run dev -- --host 0.0.0.0 --port 5173
```

浏览器打开 Vite 提示的地址（默认 http://localhost:5173）。

## 与 WorldSim

新增 [北京总院一号楼场景集](examples/beijing_zongyuan_1haolou/README.md)：396 个可编辑项目、对应 WorldSim 导出及场景集清单，支持在 Web Monitor 一次提交、顺序运行或最多 10 场景并发。
`beijing_zongyuan_1haolou` 也已加入编辑器内置地图。

| 路径 | 角色 |
|------|------|
| `simulation/scene_editor/` | Web UI + 导出样例 |
| `simulation/worldsim/` | WorldSim 数据源，读 `*.scenario.json` |
| `examples/demo.scenario.json` | WorldSim `LoadScenario` 样例 |

### 保存项目与导出仿真场景是不同操作

- **项目 → 保存 / 另存为**：保存 `*.mineproj.json`，包含 `kind: "mine-project"`、地图和编辑状态，供编辑器再次打开。改文件后缀不会转换格式。
- **项目 → 导出 WorldSim 场景…**：生成 `*.worldsim.scenario.json`，字段对齐 `simulation/worldsim/proto/scenario.proto`。主车被转换到顶层 `ego`，其他参与者和触发器转换为 protobuf 枚举；未设置的路径点朝向保持未设置，不强制为零。
- 在 Web Monitor → Sim 中选择 WorldSim、**导出的场景文件**、相同地图目录以及车辆/Profile/模块配置，再提交任务。下载文件必须先放到容器可访问的 `/apollo_workspace` 下；浏览器下载目录不一定是容器目录。
- 导出不会改变项目的保存位置。不要手工只删除 `kind`，也不要将项目文件直接传给 WorldSim。

容器中也可以转换已经保存的项目（Node >= 22.18，输出文件必须尚不存在）：

```bash
npm run export:worldsim -- examples/test.scenario.json examples/test.worldsim.scenario.json
npm run test:export
```

命令行、浏览器导出和 Apollo bridge 共用同一份转换器，不维护多套格式映射。

## 常用脚本

```bash
npm run dev          # 开发服务器
npm run build        # 生产构建
npm run preview      # 预览构建产物
npm run lint         # oxlint
npm run mock:bridge  # 本地 mock Apollo bridge
```

## 操作摘要

| 操作 | 说明 |
|---|---|
| 放置 Agent | 顶栏点类型，再在视口点击 |
| 触发器 | 顶栏下拉；Location/Distance 需点选位置 |
| 路径编辑 | 选中 Agent → Edit waypoints |
| 两点 Routing | 选中 Agent → 两点 Routing |
| Play | 空格或顶栏 Play（本地预览，非 WorldSim Tick） |
| 导入导出 | JSON 场景文件 |

## 目录

```text
scene_editor/
├── examples/          # *.scenario.json 样例（给 WorldSim）
├── public/maps/
├── src/
│   ├── app/
│   ├── components/
│   ├── core/
│   ├── map/
│   ├── scene3d/
│   └── scenarios/
├── package.json
└── vite.config.ts
```
