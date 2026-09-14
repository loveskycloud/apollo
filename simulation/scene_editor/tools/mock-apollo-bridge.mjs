#!/usr/bin/env node
/**
 * Mock Apollo Sim Bridge — 实现 v1 WebSocket JSON 协议（见 Apollo-Integration-Plan.md 第 6 节），
 * 用于在没有 Apollo 环境时联调 scenario-editor 前端。
 *
 * 用法：npm run mock:bridge  （默认 ws://localhost:8889/ws）
 *
 * 行为：
 *  - GetVehicleList/GetMapList：返回 Apollo 目录结构中的车型与地图（mock 数据）
 *  - SetVehicle：推送 VehicleParam（mkz_example 真实参数）
 *  - SetMap / GetMapElements：推送 public/maps/apollo_base_map_t204.json
 *  - SetModules：打印将拉起的 mainboard 命令，回 HmiStatus
 *  - SimControl START/STOP/RESET：虚拟车沿 planning 轨迹推进（perfect control 模拟）
 *  - SendRouting：把请求点串成折线路径，作为 ADCTrajectory 回放给前端
 *  - UploadObstacles：计数并回显到 HmiStatus
 */
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { networkInterfaces } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { WebSocketServer } from 'ws';
import { Bonjour } from 'bonjour-service';

const PORT = Number(process.env.MOCK_BRIDGE_PORT ?? 8889);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const MAP_JSON_PATH = join(ROOT, 'public', 'maps', 'apollo_base_map_t204.json');

/** 唯一服务 ID：SB-xxxxxx（首个非内部网卡 MAC 的 sha1 前 6 位），mDNS 名 simbridge-<id>.local */
function deriveServiceId() {
  const mac = Object.values(networkInterfaces())
    .flat()
    .find((i) => i && !i.internal && i.mac !== '00:00:00:00:00:00')?.mac;
  const hash = createHash('sha1').update(mac ?? String(process.pid)).digest('hex');
  return `SB-${hash.slice(0, 6)}`;
}

const SERVICE_ID = deriveServiceId();
const VEHICLES = ['mkz_example', 'mkz_121', 'mkz_lgsvl_321', 'nuscenes_165', 'kitti_140', 'nuscenes_occ'];
const MAPS = ['mock_t204', '1haolou', 'borregas_ave'];

const VEHICLE_PARAM = {
  brand: 'LINCOLN_MKZ',
  length: 4.933,
  width: 2.11,
  height: 1.48,
  minTurnRadius: 5.05,
  maxAcceleration: 2.0,
  maxDeceleration: -6.0,
  maxSteerAngle: 8.203,
  maxSteerAngleRate: 6.98,
  steerRatio: 16,
  wheelBase: 2.8448,
  wheelRollingRadius: 0.335,
  maxAbsSpeedWhenStopped: 0.2,
};

const MODULE_DAGS = {
  routing: ['modules/routing/dag/routing.dag', 'modules/external_command/process_component/dag/external_command_process.dag'],
  planning: ['modules/planning/planning_component/dag/planning.dag'],
  prediction: ['modules/prediction/dag/prediction.dag'],
  control: ['modules/control/control_component/dag/control.dag'],
};

const state = {
  modules: { planning: true, control: false, prediction: false, routing: true },
  simRunning: false,
  currentVehicle: null,
  currentMap: null,
  obstacleCount: 0,
  startPoint: null, // {x,y,z,heading}
  path: [],         // [{x,y}] 剩余轨迹
  s: 0,             // 沿程距离
  speed: 0,
  clients: new Set(),
};

function nowSec() {
  return Date.now() / 1000;
}

function densify(points, step = 1.0) {
  const out = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    const d = Math.hypot(b.x - a.x, b.y - a.y);
    const n = Math.max(1, Math.ceil(d / step));
    for (let k = 0; k < n; k += 1) {
      out.push({ x: a.x + ((b.x - a.x) * k) / n, y: a.y + ((b.y - a.y) * k) / n });
    }
  }
  out.push(points[points.length - 1]);
  return out;
}

function totalLength(points) {
  let L = 0;
  for (let i = 1; i < points.length; i += 1) {
    L += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
  }
  return L;
}

function poseAt(points, s) {
  let remain = s;
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    const d = Math.hypot(b.x - a.x, b.y - a.y);
    if (remain <= d || i === points.length - 1) {
      const t = d > 1e-6 ? Math.min(1, remain / d) : 0;
      return {
        x: a.x + (b.x - a.x) * t,
        y: a.y + (b.y - a.y) * t,
        heading: Math.atan2(b.y - a.y, b.x - a.x),
      };
    }
    remain -= d;
  }
  const last = points[points.length - 1];
  return { x: last.x, y: last.y, heading: 0 };
}

/** 梯形速度曲线：加速到 vmax，末端减速到 0 */
function speedAt(s, total, vmax = 4) {
  const accel = 1.2;
  const tAcc = (vmax * vmax) / (2 * accel);
  if (total - s < tAcc) {
    return Math.max(0.3, Math.sqrt(Math.max(0, 2 * accel * (total - s))));
  }
  if (s < tAcc) return Math.max(0.5, Math.sqrt(Math.max(0, 2 * accel * s)));
  return vmax;
}

function send(ws, type, data) {
  if (ws.readyState === 0) return;
  ws.send(JSON.stringify({ type, data }));
}

function broadcast(type, data) {
  const payload = JSON.stringify({ type, data });
  for (const ws of state.clients) {
    if (ws.readyState === 1) ws.send(payload);
  }
}

function hmiStatus() {
  return {
    simRunning: state.simRunning,
    modules: state.modules,
    currentVehicle: state.currentVehicle,
    currentMap: state.currentMap,
    obstacleCount: state.obstacleCount,
    serviceId: SERVICE_ID,
    mode: 'mock',
  };
}

function pushHmiStatus() {
  broadcast('HmiStatus', hmiStatus());
}

function publishEgoState() {
  if (!state.simRunning || state.path.length < 2) return;
  const total = totalLength(state.path);
  if (state.s >= total) {
    state.speed = 0;
    state.simRunning = false;
    const end = poseAt(state.path, total);
    broadcast('EgoState', { ...end, z: 0.4, speed: 0, timestampSec: nowSec() });
    broadcast('PlanningTrajectory', { sequenceNum: 0, points: [] });
    pushHmiStatus();
    console.log('[mock] 轨迹走完，仿真停止');
    return;
  }
  const v = speedAt(state.s, total);
  state.speed = v;
  const pose = poseAt(state.path, state.s);
  broadcast('EgoState', { ...pose, z: 0.4, speed: v, timestampSec: nowSec() });
}

function publishTrajectory() {
  if (!state.simRunning || state.path.length < 2) return;
  const total = totalLength(state.path);
  // 截取前方 60m 的路径点作为 planning 轨迹
  const trimmed = [];
  let acc = 0;
  let started = false;
  for (let i = 1; i < state.path.length; i += 1) {
    const a = state.path[i - 1];
    const b = state.path[i];
    const d = Math.hypot(b.x - a.x, b.y - a.y);
    if (!started && acc + d >= state.s) started = true;
    if (started) trimmed.push({ x: b.x, y: b.y });
    acc += d;
    if (acc - state.s > 60) break;
  }
  if (trimmed.length < 2) return;
  const points = trimmed.map((p, idx) => ({
    ...p,
    theta: 0,
    v: speedAt(state.s + (idx * 60) / Math.max(trimmed.length, 1), total),
    relativeTime: idx * 0.1,
  }));
  broadcast('PlanningTrajectory', {
    sequenceNum: Date.now() % 100000,
    points,
  });
}

function setPathFromPoints(points, startPose) {
  const waypoints = [];
  if (startPose) waypoints.push({ x: startPose.x, y: startPose.y });
  for (const p of points) waypoints.push({ x: p.x, y: p.y });
  state.path = densify(waypoints);
  state.s = 0;
}

function handleMessage(ws, raw) {
  let env;
  try {
    env = JSON.parse(raw);
  } catch {
    return;
  }
  const { type, data = {} } = env;
  const reply = (payload) => send(ws, type, { requestId: data.requestId, ...payload });
  const log = (...args) => console.log(`[mock] ${type}:`, ...args);

  switch (type) {
    case 'GetVehicleList':
      reply({ vehicles: VEHICLES });
      break;
    case 'GetMapList':
      reply({ maps: MAPS });
      break;
    case 'GetVehicleParam':
      reply({ ...VEHICLE_PARAM });
      break;
    case 'SetVehicle': {
      state.currentVehicle = data.vehicle ?? null;
      broadcast('VehicleParam', VEHICLE_PARAM);
      log('切换车型 →', state.currentVehicle);
      reply({});
      pushHmiStatus();
      break;
    }
    case 'SetMap': {
      state.currentMap = data.map ?? null;
      log('切换地图 →', state.currentMap);
      reply({});
      if (state.currentMap) {
        try {
          const mapJson = JSON.parse(readFileSync(MAP_JSON_PATH, 'utf8'));
          broadcast('MapData', { map: mapJson, name: `${state.currentMap} (via bridge)` });
          console.log('[mock] MapData 已推送（t204 mock 图）');
        } catch (err) {
          send(ws, 'Error', { message: `读取地图失败: ${err.message}` });
        }
      }
      pushHmiStatus();
      break;
    }
    case 'GetMapElements': {
      try {
        const mapJson = JSON.parse(readFileSync(MAP_JSON_PATH, 'utf8'));
        reply({ map: mapJson, name: state.currentMap ?? 'mock_t204' });
      } catch (err) {
        send(ws, 'Error', { message: String(err.message) });
      }
      break;
    }
    case 'SetModules': {
      state.modules = { ...state.modules, ...data.modules };
      for (const [mod, on] of Object.entries(state.modules)) {
        const dags = MODULE_DAGS[mod] ?? [];
        for (const dag of dags) {
          console.log(`[mock] ${on ? '启动' : '停止'}: ${on ? `nohup mainboard -d ${dag} &` : `pkill -f ${dag.split('/').pop()}`}`);
        }
      }
      reply({ modules: state.modules });
      pushHmiStatus();
      break;
    }
    case 'SimControl': {
      const { action, startPoint, modules } = data;
      if (action === 'START') {
        // 模块在仿真发起时才拉起（勾选仅记录）
        if (modules) {
          state.modules = { ...state.modules, ...modules };
          for (const [mod, on] of Object.entries(state.modules)) {
            if (on) console.log(`[mock] 仿真发起：拉起模块 ${mod}（mainboard）`);
          }
        }
        // 无路由时不生成默认轨迹（真实 planning 仅在收到路由指令后才输出轨迹），
        // 虚拟车停在起点持续发布 localization，等待 SendRouting。
        state.path = [];
        state.s = 0;
        state.speed = 0;
        state.startPoint = startPoint ?? state.startPoint;
        state.simRunning = true;
        log('START @', state.startPoint);
      } else if (action === 'STOP') {
        state.simRunning = false;
        state.speed = 0;
        log('STOP');
      } else if (action === 'RESET') {
        state.s = 0;
        state.speed = 0;
        if (state.startPoint) {
          broadcast('EgoState', { ...state.startPoint, speed: 0, timestampSec: nowSec() });
        }
        state.simRunning = false;
        log('RESET');
      }
      reply({});
      pushHmiStatus();
      break;
    }
    case 'SendRouting': {
      const pts = Array.isArray(data.points) ? data.points : [];
      log('Routing 请求点:', pts.length);
      if (pts.length >= 2) {
        const sp = state.startPoint;
        setPathFromPoints(pts, sp);
        state.simRunning = true;
        console.log(`[mock] 生成路径 ${totalLength(state.path).toFixed(1)}m，开始仿真`);
      }
      reply({ accepted: pts.length >= 2 });
      pushHmiStatus();
      break;
    }
    case 'Ping':
      send(ws, 'Pong', { t: data.t ?? 0 });
      break;
    case 'UploadObstacles': {
      state.obstacleCount = Array.isArray(data.obstacles) ? data.obstacles.length : 0;
      break;
    }
    default:
      console.log('[mock] 未知消息类型:', type);
      break;
  }
}

const wss = new WebSocketServer({ port: PORT, path: '/ws' });
wss.on('connection', (ws) => {
  console.log('[mock] 前端已连接');
  state.clients.add(ws);
  send(ws, 'HmiStatus', hmiStatus());
  ws.on('message', (raw) => handleMessage(ws, raw.toString()));
  ws.on('close', () => state.clients.delete(ws));
});

setInterval(() => {
  if (!state.simRunning) return;
  // 50ms 步进：按当前速度推进沿程距离（perfect control 模拟）
  state.s += state.speed * 0.05;
  publishEgoState();
}, 50); // 20Hz EgoState

setInterval(() => {
  if (!state.simRunning) return;
  publishTrajectory();
}, 100); // 10Hz PlanningTrajectory

console.log(`[mock] Mock Apollo Sim Bridge 已启动: ws://localhost:${PORT}/ws`);
console.log(`[mock] 唯一服务 ID: ${SERVICE_ID}（mDNS: simbridge-${SERVICE_ID.slice(3).toLowerCase()}.local）`);
console.log('[mock] 地图文件:', MAP_JSON_PATH);

// mDNS 广播：其他设备可用 SB-xxxxxx 唯一地址连接（浏览器解析 *.local）
try {
  const bonjour = new Bonjour();
  bonjour.publish({ name: `simbridge-${SERVICE_ID.slice(3).toLowerCase()}`, type: 'simbridge', port: PORT });
  console.log('[mock] mDNS 服务已广播 (_simbridge._tcp)');
} catch (err) {
  console.warn('[mock] mDNS 广播失败（不影响本机连接）:', err.message);
}
