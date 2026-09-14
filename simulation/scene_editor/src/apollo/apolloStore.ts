import { create } from 'zustand';
import { useScenarioStore } from '../core/store';
import { isAgentActive } from '../core/agentActive';
import { parseApolloBaseMapJson } from '../map/loaders/apolloBaseMap';
import { isPlausibleRoutePath } from '../map/routeAlongLanes';
import type { HdMap, Vec3 } from '../core/types';
import { buildWorldSimScenarioPayload } from '../scenarios/exportWorldSimScenario';
import { toRenderCoords } from './coords';
import {
  newRequestId,
  type ApolloEnvelope,
  type ApolloModuleKey,
  type ControlCommandInfo,
  type EgoPose,
  type HmiStatusInfo,
  type ObstaclePose,
  type ObstacleTrajPoint,
  type PncModules,
  type PredictionObstacleTraj,
  type RoutingPathInfo,
  type RoutingPoint,
  type SimControlData,
  type TrajPoint,
  type VehicleParamInfo,
} from './protocol';

export type ApolloStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

interface PendingRequest {
  resolve: (data: Record<string, unknown>) => void;
  reject: (err: Error) => void;
  timer: number;
}

interface ApolloStore {
  /** bridge 上报的唯一服务 ID（SB-xxxxxx，由 MAC 派生） */
  serviceId: string | null;
  /** 唯一地址输入：空 = 本机默认；SB-xxxxxx / host.local / host:port / ws://... */
  addressInput: string;
  status: ApolloStatus;
  error: string | null;
  log: string[];

  /** 仿真模式（编辑模式关闭）：右侧出现「仿真配置」标签页 */
  simMode: boolean;

  /** 待应用的模块勾选（发送给 bridge） */
  modules: PncModules;
  /** bridge 上报的实际运行状态 */
  runningModules: PncModules;
  simRunning: boolean;
  currentVehicle: string | null;
  currentMap: string | null;
  vehicles: string[];
  maps: string[];
  vehicleParam: VehicleParamInfo | null;

  egoPose: EgoPose | null;
  egoSpeed: number;
  /** Planning ADCTrajectory（仅仿真中绘制） */
  trajectory: TrajPoint[];
  /** Prediction 障碍物轨迹（仅仿真中绘制） */
  predictionTrajectories: PredictionObstacleTraj[];
  lastControl: ControlCommandInfo | null;
  obstacleCount: number;
  /** 'apollo' = 真实算法闭环；'mock' = Mock 动画（仅联调 UI） */
  mode: 'apollo' | 'mock' | null;

  /** 仿真模式下沿车道点选的路由点（预览渲染，两点后自动下发） */
  routingPoints: RoutingPoint[];
  /** Apollo routing 中心线（PlanningCommand → MapService，与 dreamview 红线路径同源） */
  routingPath: Vec3[];
  routingTime: number;

  setAddressInput: (v: string) => void;
  setSimMode: (v: boolean) => void;
  connect: (address?: string) => void;
  disconnect: () => void;
  request: (type: string, data?: Record<string, unknown>, timeoutMs?: number) => Promise<Record<string, unknown>>;
  send: (type: string, data?: Record<string, unknown>) => void;

  setModule: (key: ApolloModuleKey, value: boolean) => void;
  setVehicle: (vehicle: string) => void;
  setMap: (map: string) => void;
  simControl: (action: SimControlData['action']) => void;

  /** 仿真模式下 Routing 第二点后调用：把路由点下发 Apollo */
  sendRoutingPoints: () => void;
  pushRoutingPoint: (p: Vec3, heading?: number) => void;
  clearRoutingPoints: () => void;
  /** 编辑态 Route（路径与行为）一键下发 Apollo */
  sendRouteToApollo: (points: RoutingPoint[]) => void;
  /** 非仿真时向 Apollo 请求路由预览（沿车道中心线） */
  previewActiveRoute: () => Promise<void>;
  pushLog: (line: string) => void;
}

// WebSocket、pending、心跳、重连放模块作用域，不进入响应式状态。
let ws: WebSocket | null = null;
const pending = new Map<string, PendingRequest>();
let logSeq = 0;
let wantConnected = false;

/** 场景路点 → SendRouting 点列（保留编辑器记录的 heading） */
function routingPointsFromWaypoints(
  waypoints: { position: Vec3; heading?: number }[],
): RoutingPoint[] {
  return waypoints.map((w) => ({
    x: w.position.x,
    y: w.position.y,
    ...(w.heading != null ? { heading: w.heading } : {}),
  }));
}
let reconnectTimer: number | null = null;
let heartbeatTimer: number | null = null;
let reconnectAttempts = 0;
let lastMessageAt = 0;
let currentUrl = '';
let previewRouteTimer: number | null = null;

/** 路点/主车变更后防抖请求 Apollo 路由预览（非仿真态） */
export function scheduleRoutePreview() {
  if (previewRouteTimer != null) {
    window.clearTimeout(previewRouteTimer);
  }
  previewRouteTimer = window.setTimeout(() => {
    previewRouteTimer = null;
    void useApolloStore.getState().previewActiveRoute();
  }, 500);
}

const DEFAULT_MODULES = {
  planning: true,
  control: true,
  prediction: true,
  routing: true,
};

const DEFAULT_PORT = 8889;

function sameHostUrl(): string {
  const { protocol, hostname } = window.location;
  const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:';
  return `${wsProtocol}//${hostname}:${DEFAULT_PORT}/ws`;
}

/**
 * 唯一地址解析（不出现 IP 的寻址，见 Apollo-Integration-Plan.md 连接寻址方案）：
 * - 空 / localhost      → 同机默认 ws://<page-host>:8889/ws
 * - SB-3fa19c           → ws://simbridge-3fa19c.local:8889/ws（mDNS 唯一服务名）
 * - simbridge-x.local   → ws://simbridge-x.local:8889/ws
 * - host[:port]         → ws://host[:port]/ws
 * - ws://...            → 原样
 */
export function resolveAddress(input: string): string {
  const raw = input.trim();
  if (!raw) return sameHostUrl();
  if (/^wss?:\/\//i.test(raw)) return raw;
  if (/^sb-[0-9a-f]{6}$/i.test(raw)) {
    return `ws://simbridge-${raw.slice(3).toLowerCase()}.local:${DEFAULT_PORT}/ws`;
  }
  // host 或 host:port（含 *.local 域名与 IP）
  const host = raw.replace(/\/+$/, '').replace(/\/ws$/, '');
  const withPort = host.includes(':') ? host : `${host}:${DEFAULT_PORT}`;
  return `ws://${withPort}/ws`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

/** EgoState / VehicleParam / MapData 到场景仓库的落地 */
function applyEgoState(pose: EgoPose, speed: number) {
  if (!useApolloStore.getState().simRunning) return;
  const scenario = useScenarioStore.getState();
  const ego = scenario.scenario.agents.find((a) => a.type === 'ego');
  if (!ego) return;
  scenario.setRuntimeAgent(ego.id, {
    position: { x: pose.x, y: pose.y, z: ego.size.z / 2 },
    heading: pose.heading,
    speed,
    moving: true,
  });
}

function applyVehicleParam(param: VehicleParamInfo) {
  const store = useScenarioStore.getState();
  const ego = store.scenario.agents.find((a) => a.type === 'ego');
  const length = param.length;
  const width = param.width;
  const height = param.height;
  if (!ego || !length || !width || !height) return;
  store.updateAgent(ego.id, {
    size: { x: length, y: width, z: height },
  });
  store.setRuntimeAgent(ego.id, { position: { ...ego.position, z: height / 2 } });
}

function applyMapData(payload: Record<string, unknown>) {
  const mapJson = (payload.map ?? payload) as Parameters<typeof parseApolloBaseMapJson>[0];
  const name = typeof payload.name === 'string' ? payload.name : 'Apollo Map (bridge)';
  const map: HdMap = parseApolloBaseMapJson(mapJson, name);
  useScenarioStore.setState({
    map,
    scenario: { ...useScenarioStore.getState().scenario, mapId: map.id },
  });
  scheduleRoutePreview();
}

function stopHeartbeat() {
  if (heartbeatTimer != null) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function startHeartbeat() {
  stopHeartbeat();
  lastMessageAt = Date.now();
  heartbeatTimer = window.setInterval(() => {
    const socket = ws;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    // 长链接保活：30s 无任何消息则断开触发重连
    if (Date.now() - lastMessageAt > 30_000) {
      socket.close();
      return;
    }
    socket.send(JSON.stringify({ type: 'Ping', data: { t: Date.now() } }));
  }, 10_000);
}

function scheduleReconnect() {
  if (!wantConnected || reconnectTimer != null) return;
  reconnectAttempts += 1;
  const delay = Math.min(10_000, 1000 * 2 ** (reconnectAttempts - 1));
  // 重连日志降噪：仅首次与每 10 次提示一次
  if (reconnectAttempts === 1 || reconnectAttempts % 10 === 0) {
    useApolloStore
      .getState()
      .pushLog(`断线，${(delay / 1000).toFixed(0)}s 后重连`);
  }
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    useApolloStore.getState().connect();
  }, delay);
}

function handleEnvelope(env: ApolloEnvelope) {
  const store = useApolloStore.getState();
  const { type, data } = env;
  const requestId = data?.requestId;

  if (requestId && pending.has(requestId)) {
    const p = pending.get(requestId)!;
    clearTimeout(p.timer);
    pending.delete(requestId);
    p.resolve(data);
    return;
  }

  switch (type) {
    case 'Pong':
    case 'Ping': {
      if (type === 'Ping' && ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'Pong', data: {} }));
      }
      break;
    }
    case 'HmiStatus': {
      const info = data as unknown as HmiStatusInfo;
      const nextId = info.serviceId ?? store.serviceId;
      const trimmed = store.addressInput.trim();
      // 输入框默认展示当前连接标识；用户已改成其它值时不覆盖
      const nextAddress =
        nextId && (!trimmed || trimmed === (store.serviceId ?? ''))
          ? nextId
          : store.addressInput;
      useApolloStore.setState({
        simRunning: info.simRunning ?? false,
        runningModules: info.modules ?? store.runningModules,
        currentVehicle: info.currentVehicle ?? store.currentVehicle,
        currentMap: info.currentMap ?? store.currentMap,
        obstacleCount: info.obstacleCount ?? store.obstacleCount,
        serviceId: nextId,
        addressInput: nextAddress,
        mode: info.mode ?? store.mode,
        ...(info.simRunning
          ? {}
          : { trajectory: [], predictionTrajectories: [] }),
      });
      if (!info.simRunning) {
        scheduleRoutePreview();
      }
      break;
    }
    case 'VehicleParam': {
      const param = data as unknown as VehicleParamInfo;
      useApolloStore.setState({ vehicleParam: param });
      applyVehicleParam(param);
      break;
    }
    case 'SimEvent': {
      const message = String(data.message ?? data.event ?? '');
      if (message) store.pushLog(message);
      if (String(data.event ?? '') === 'arrived') {
        stopFeedGtObstacles();
        useApolloStore.setState({
          simRunning: false,
          trajectory: [],
          predictionTrajectories: [],
          runningModules: {
            routing: false,
            planning: false,
            prediction: false,
            control: false,
          },
        });
      }
      if (String(data.event ?? '') === 'routing_failed') {
        stopFeedGtObstacles();
        useApolloStore.setState({
          simRunning: false,
          runningModules: {
            routing: false,
            planning: false,
            prediction: false,
            control: false,
          },
        });
      }
      break;
    }
    case 'MapData': {
      applyMapData(data);
      break;
    }
    case 'EgoState': {
      const pose = {
        x: Number(data.x ?? 0),
        y: Number(data.y ?? 0),
        z: Number(data.z ?? 0),
        heading: Number(data.heading ?? 0),
      };
      const speed = Number(data.speed ?? 0);
      useApolloStore.setState({ egoPose: pose, egoSpeed: speed });
      applyEgoState(pose, speed);
      break;
    }
    case 'AgentsState': {
      // WorldSim Agent::Tick owns motion; editor only mirrors poses.
      const agents = (data.agents ?? []) as Array<Record<string, unknown>>;
      const storeRt = useScenarioStore.getState();
      for (const a of agents) {
        const id = String(a.id ?? '');
        if (!id) continue;
        const moving = Boolean(a.moving);
        const speed = Number(a.speed ?? 0);
        // 旧 binary 可能不下发 enabled：开始运动即视为已激活（恢复颜色 / 预测）
        let enabled: boolean | undefined;
        if (a.enabled !== undefined) {
          enabled = Boolean(a.enabled);
        } else if (moving || speed > 1e-3) {
          enabled = true;
        }
        storeRt.setRuntimeAgent(id, {
          position: {
            x: Number(a.x ?? 0),
            y: Number(a.y ?? 0),
            z: Number(a.z ?? 0),
          },
          heading: Number(a.heading ?? 0),
          speed,
          moving,
          ...(enabled !== undefined ? { enabled } : {}),
        });
      }
      useApolloStore.setState({ obstacleCount: agents.length });
      break;
    }
    case 'RoutingPath': {
      const info = data as unknown as RoutingPathInfo;
      const pts: Vec3[] = [];
      for (const seg of info.routePath ?? []) {
        for (const p of seg.point ?? []) {
          pts.push({ x: Number(p.x), y: Number(p.y), z: Number(p.z ?? 0) });
        }
      }
      useApolloStore.setState({
        routingPath: pts,
        routingTime: Number(info.routingTime ?? 0),
      });
      if (pts.length >= 2 && !isPlausibleRoutePath(pts)) {
        store.pushLog(`Apollo routing 点过少（${pts.length}），使用本地车道展开`);
      } else if (pts.length >= 2) {
        store.pushLog(`Apollo routing 路径已更新（${pts.length} 点）`);
      }
      break;
    }
    case 'PlanningTrajectory': {
      const points = ((data.points ?? []) as unknown as TrajPoint[]).map((pt) => {
        const local = toRenderCoords(pt);
        return { ...pt, x: local.x, y: local.y, z: local.z };
      });
      useApolloStore.setState({ trajectory: points });
      break;
    }
    case 'PredictionObstacles': {
      const raw = (data.obstacles ?? []) as unknown as Array<{
        id?: number | string;
        trajectories?: Array<{ points?: TrajPoint[] }>;
      }>;
      const predictionTrajectories: PredictionObstacleTraj[] = [];
      for (const obs of raw) {
        const trajs = obs.trajectories ?? [];
        for (let i = 0; i < trajs.length; i += 1) {
          const pts = (trajs[i].points ?? []).map((pt) => {
            const local = toRenderCoords(pt);
            return { ...pt, x: local.x, y: local.y, z: local.z };
          });
          if (pts.length < 2) continue;
          predictionTrajectories.push({
            id: `${obs.id ?? 'obs'}_${i}`,
            points: pts,
          });
        }
      }
      useApolloStore.setState({ predictionTrajectories });
      break;
    }
    case 'ControlCommand': {
      useApolloStore.setState({
        lastControl: data as unknown as ControlCommandInfo,
      });
      break;
    }
    case 'RuntimeLog': {
      const level = String(data.level ?? 'info');
      const message = String(data.message ?? '');
      if (!message) break;
      const prefix =
        level === 'error' ? '[错误] ' : level === 'warn' ? '[警告] ' : '';
      store.pushLog(`${prefix}${message}`);
      break;
    }
    case 'Status':
    case 'Error': {
      store.pushLog(`[${type}] ${String(data.message ?? JSON.stringify(data))}`);
      break;
    }
    default:
      break;
  }
}


let feedGtObstaclesTimer: number | null = null;
/** When true, WorldSim owns agent motion; do not UploadObstacles poses. */
let worldAgentsActive = false;

function stopFeedGtObstacles() {
  if (feedGtObstaclesTimer != null) {
    window.clearInterval(feedGtObstaclesTimer);
    feedGtObstaclesTimer = null;
  }
}

function startFeedGtObstacles() {
  stopFeedGtObstacles();
  // Legacy fallback only: WorldSim Agent Tick is the preferred path.
  if (worldAgentsActive) {
    return;
  }
  feedGtObstaclesTimer = window.setInterval(() => {
    const st = useApolloStore.getState();
    if (
      worldAgentsActive ||
      !st.simRunning ||
      !st.modules.prediction ||
      st.status !== 'connected'
    ) {
      return;
    }
    st.send('UploadObstacles', { obstacles: collectObstacles() });
  }, 100);
}

export const useApolloStore = create<ApolloStore>((set, get) => ({
  serviceId: null,
  addressInput: '',
  status: 'disconnected',
  error: null,
  log: [],

  simMode: false,

  modules: { ...DEFAULT_MODULES },
  runningModules: { planning: false, control: false, prediction: false, routing: false },
  simRunning: false,
  currentVehicle: null,
  currentMap: null,
  vehicles: [],
  maps: [],
  vehicleParam: null,

  egoPose: null,
  egoSpeed: 0,
  trajectory: [],
  predictionTrajectories: [],
  lastControl: null,
  obstacleCount: 0,
  mode: null,

  routingPoints: [],
  routingPath: [],
  routingTime: 0,

  setAddressInput: (v) => set({ addressInput: v }),

  setSimMode: (v) => set({ simMode: v }),

  pushLog: (line) =>
    set((s) => ({ log: [...s.log.slice(-99), `${++logSeq}. ${line}`] })),

  connect: (address) => {
    if (address != null) {
      set({ addressInput: address });
    }
    const url = resolveAddress(get().addressInput);
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      if (url === currentUrl) return;
      ws.close(); // 地址变更：关闭旧连接，走重连逻辑用新地址
    }
    wantConnected = true;
    currentUrl = url;
    set({ status: 'connecting', error: null });
    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch (err) {
      set({ status: 'error', error: String(err) });
      scheduleReconnect();
      return;
    }
    ws = socket;

    socket.onopen = () => {
      reconnectAttempts = 0;
      set({ status: 'connected', error: null });
      get().pushLog(`已连接（${url}）`);
      startHeartbeat();
      scheduleRoutePreview();
      // 连接后拉取列表（bridge 会推 HmiStatus 当前状态）
      void get().request('GetVehicleList').then((d) => {
        const list = (d.vehicles as string[]) ?? [];
        set({
          vehicles: list,
          currentVehicle:
            (d.current as string | undefined) ?? get().currentVehicle,
        });
        if (list.length === 0) {
          get().pushLog('车辆配置列表为空，请检查 em profile list');
        }
      }).catch((err) => get().pushLog(`拉取车辆列表失败: ${String(err)}`));
      void get().request('GetMapList').then((d) => {
        set({ maps: (d.maps as string[]) ?? [] });
      }).catch(() => undefined);
    };

    socket.onmessage = (event) => {
      lastMessageAt = Date.now();
      try {
        const env = JSON.parse(event.data as string) as ApolloEnvelope;
        handleEnvelope(env);
      } catch (err) {
        get().pushLog(`消息解析失败: ${String(err)}`);
      }
    };

    socket.onerror = () => {
      set({ status: 'error', error: 'WebSocket 连接失败' });
    };

    socket.onclose = () => {
      stopHeartbeat();
      if (ws === socket) {
        ws = null;
        set({ status: 'disconnected', simRunning: false, routingPath: [], routingTime: 0 });
      }
      scheduleReconnect();
    };
  },

  disconnect: () => {
    wantConnected = false;
    if (reconnectTimer != null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    stopHeartbeat();
    ws?.close();
    ws = null;
    set({ status: 'disconnected', routingPath: [], routingTime: 0 });
    get().pushLog('已手动断开（不再自动重连）');
  },

  request: (type, data = {}, timeoutMs = 10_000) => {
    return new Promise((resolve, reject) => {
      const socket = ws;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        reject(new Error('未连接 bridge'));
        return;
      }
      const requestId = newRequestId();
      const timer = window.setTimeout(() => {
        pending.delete(requestId);
        reject(new Error(`请求超时: ${type}`));
      }, timeoutMs);
      pending.set(requestId, { resolve, reject, timer });
      socket.send(JSON.stringify({ type, data: { ...data, requestId } }));
    });
  },

  send: (type, data = {}) => {
    const socket = ws;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type, data }));
  },

  setModule: (key, value) => {
    // 仅记录勾选（随场景保存），模块在启动仿真时才拉起
    const modules = { ...get().modules, [key]: value };
    set({ modules });
    useScenarioStore.getState().updateSimModules(modules);
  },

  setVehicle: (vehicle) => {
    get()
      .request('SetVehicle', { vehicle }, 60_000)
      .then(() => {
        set({ currentVehicle: vehicle });
        get().pushLog(`车辆配置已切换：${vehicle}`);
        return get().request('GetVehicleParam');
      })
      .then((param) => {
        if (param && Object.keys(param).length > 0) {
          useApolloStore.setState({
            vehicleParam: param as unknown as VehicleParamInfo,
          });
          applyVehicleParam(param as unknown as VehicleParamInfo);
        }
      })
      .catch((err) => get().pushLog(String(err)));
  },

  setMap: (map) => {
    get()
      .request('SetMap', { map })
      .then(() => get().pushLog(`地图已切换: ${map}`))
      .catch((err) => get().pushLog(String(err)));
  },

  simControl: (action) => {
    let startPoint: SimControlData['startPoint'];
    if (action === 'START' || action === 'RESET') {
      const store = useScenarioStore.getState();
      const ego = store.scenario.agents.find((a) => a.type === 'ego');
      // 重置/启动一律用场景编辑位姿，不用 runtime（仿真中 runtime 已漂移）
      const pos = ego?.position;
      if (pos) {
        startPoint = {
          x: pos.x,
          y: pos.y,
          z: pos.z,
          heading: ego?.heading ?? 0,
        };
      }
    }

    const runControl = () =>
      get()
        .request('SimControl', { action, startPoint, modules: get().modules }, 120_000)
        .then(async (result) => {
          get().pushLog(`仿真 ${action}`);
          if (action === 'STOP' || action === 'RESET') {
            worldAgentsActive = false;
            stopFeedGtObstacles();
            set({
              trajectory: [],
              predictionTrajectories: [],
              egoSpeed: 0,
              ...(action === 'RESET' || action === 'STOP'
                ? {
                    runningModules: {
                      routing: false,
                      planning: false,
                      prediction: false,
                      control: false,
                    },
                  }
                : {}),
            });
            const store = useScenarioStore.getState();
            for (const agent of store.scenario.agents) {
              store.setRuntimeAgent(agent.id, {
                position: { ...agent.position, z: agent.size.z / 2 },
                heading: agent.heading,
                speed: agent.type === 'ego' ? 0 : agent.speed,
                moving: false,
              });
            }
            if (action === 'RESET') {
              get().pushLog('已重置到场景起点');
            }
            return;
          }
          if (action !== 'START') return;
          if (result?.ready) {
            get().pushLog(
              `配置与模块已就绪${result.profile ? `（${String(result.profile)}）` : ''}`,
            );
          } else if (result?.message) {
            get().pushLog(String(result.message));
          }
          // Agent 运动由 WorldSim Tick；仅 LoadScenario 失败时回退 UploadObstacles
          if (get().modules.prediction && !worldAgentsActive) {
            startFeedGtObstacles();
            get().pushLog('回退：前端 UploadObstacles（未加载 World 场景）');
          } else {
            stopFeedGtObstacles();
            if (worldAgentsActive) {
              get().pushLog('障碍物由 WorldSim Agent Tick 驱动');
            }
          }
          const store = useScenarioStore.getState();
          const ego = store.scenario.agents.find((a) => a.type === 'ego');
          const route = ego?.routes.find((r) => r.id === ego.activeRouteId);
          const waypoints = route?.waypoints ?? [];
          if (waypoints.length < 1 || !get().modules.routing) {
            get().pushLog('无 Route 或未勾选 Routing，跳过路由');
            return;
          }
          await sleep(300);
          const points = routingPointsFromWaypoints(waypoints);
          const routingStart = ego
            ? {
                x: ego.position.x,
                y: ego.position.y,
                z: ego.position.z,
                heading: ego.heading,
              }
            : undefined;
          try {
            const routing = await get().request(
              'SendRouting',
              { points, startPoint: routingStart },
              25_000,
            );
            const ok = routing.ok !== false && routing.accepted !== false;
            const msg = String(routing.message ?? '');
            if (ok) {
              get().pushLog(`路由已下发（${points.length} 点）${msg ? '：' + msg : ''}`);
            } else {
              get().pushLog(`路由失败，仿真已停止：${msg || JSON.stringify(routing)}`);
              set({
                simRunning: false,
                runningModules: {
                  routing: false,
                  planning: false,
                  prediction: false,
                  control: false,
                },
              });
            }
          } catch (err) {
            get().pushLog(String(err));
          }
        })
        .catch((err) => get().pushLog(String(err)));

    if (action !== 'START') {
      void runControl();
      return;
    }

    // START 前先把场景交给 WorldSim，由后端 Agent::Tick 推进行人/车辆
    const scenarioStore = useScenarioStore.getState();
    let payload: Record<string, unknown>;
    try {
      payload = buildWorldSimScenarioPayload(scenarioStore.scenario);
    } catch (err) {
      get().pushLog(`场景校验失败，未启动仿真：${String(err)}`);
      return;
    }
    get()
      .request('LoadScenario', { scenario: payload }, 30_000)
      .then((load) => {
        const ok = load?.ok !== false;
        if (ok) {
          worldAgentsActive = true;
          stopFeedGtObstacles();
          get().pushLog(
            `LoadScenario 成功（agents=${String(load.agents ?? '?')}），后端 Agent 负责移动`,
          );
        } else {
          worldAgentsActive = false;
          get().pushLog(
            `LoadScenario 失败，将回退 UploadObstacles：${String(load?.message ?? '')}`,
          );
        }
        return runControl();
      })
      .catch((err) => {
        worldAgentsActive = false;
        get().pushLog(`LoadScenario 异常，回退 UploadObstacles：${String(err)}`);
        void runControl();
      });
  },

  sendRoutingPoints: () => {
    const points = get().routingPoints;
    if (points.length < 2) return;
    get()
      .request('SendRouting', { points }, 25_000)
      .then((result) => {
        const ok = result.ok !== false && result.accepted !== false;
        const msg = String(result.message ?? '');
        get().pushLog(
          `${ok ? '路由已下发' : '路由失败'}（${points.length} 点）${msg ? '：' + msg : ''}`,
        );
      })
      .catch((err) => get().pushLog(String(err)));
  },

  pushRoutingPoint: (p, heading) => {
    const pt: RoutingPoint = {
      x: p.x,
      y: p.y,
      ...(heading != null ? { heading } : {}),
    };
    const points = [...get().routingPoints, pt];
    if (points.length < 2) {
      set({ routingPoints: points });
      return;
    }
    // 两点即完整路由：预览终点 → 下发 → 清空
    set({ routingPoints: points });
    get().sendRoutingPoints();
    set({ routingPoints: [] });
  },

  clearRoutingPoints: () => set({ routingPoints: [] }),

  sendRouteToApollo: (points) => {
    if (points.length < 2) return;
    get()
      .request('SendRouting', { points }, 25_000)
      .then((result) => {
        const ok = result.ok !== false && result.accepted !== false;
        get().pushLog(
          `${ok ? '路由已下发' : '路由失败'}（${points.length} 点）${result.message ? '：' + String(result.message) : ''}`,
        );
      })
      .catch((err) => get().pushLog(String(err)));
  },

  previewActiveRoute: async () => {
    const st = get();
    if (st.status !== 'connected' || st.simRunning) return;
    const scenario = useScenarioStore.getState().scenario;
    const ego = scenario.agents.find((a) => a.type === 'ego');
    if (!ego) return;
    const route = ego.routes.find((r) => r.id === ego.activeRouteId);
    const waypoints = route?.waypoints ?? [];
    if (waypoints.length < 1) {
      set({ routingPath: [], routingTime: 0 });
      return;
    }
    const startPoint = {
      x: ego.position.x,
      y: ego.position.y,
      z: ego.position.z,
      heading: ego.heading,
    };
    const points = routingPointsFromWaypoints(waypoints);
    try {
      const result = await get().request(
        'PreviewRouting',
        { startPoint, points },
        30_000,
      );
      const ok = result.ok !== false && result.accepted !== false;
      if (!ok) {
        get().pushLog(`路由预览失败：${String(result.message ?? '')}`);
      }
    } catch {
      // 预览为后台请求，连接抖动时不刷屏
    }
  },

}));

/** 收集非 Ego：位姿 + 预定路径，供 fake_prediction 发真值预测 */
export function collectObstacles(): ObstaclePose[] {
  const store = useScenarioStore.getState();
  const obstacles: ObstaclePose[] = [];
  for (const agent of store.scenario.agents) {
    if (agent.type === 'ego') continue;
    const rt = store.runtime[agent.id];
    // Disable / 未激活：不发预测轨迹
    if (!isAgentActive(agent, rt)) continue;
    const pos = rt?.position ?? agent.position;
    const heading = rt?.heading ?? agent.heading;
    const speed = Math.max(rt?.speed ?? agent.speed, 0);
    const enu = pos;
    obstacles.push({
      id: agent.id,
      type: agent.type,
      x: enu.x,
      y: enu.y,
      z: enu.z,
      heading,
      speed,
      length: agent.size.x,
      width: agent.size.y,
      height: agent.size.z,
      trajectory: buildAgentGtTrajectory(agent, pos, heading, speed),
    });
  }
  return obstacles;
}

function buildAgentGtTrajectory(
  agent: {
    routes: { id: string; waypoints: { position: { x: number; y: number; z?: number } }[] }[];
    activeRouteId?: string;
  },
  pos: { x: number; y: number; z?: number },
  heading: number,
  speed: number,
): ObstacleTrajPoint[] {
  const route =
    agent.routes.find((r) => r.id === agent.activeRouteId) ?? agent.routes[0];
  const wps = route?.waypoints ?? [];
  // Speed-only (no path): short constant-velocity ray for fake_prediction.
  if (speed <= 1e-3) return [];
  if (wps.length === 0) {
    const v = speed;
    const out: ObstacleTrajPoint[] = [];
    const horizon = 8; // seconds
    const steps = 16;
    for (let i = 0; i <= steps; i++) {
      const t = (horizon * i) / steps;
      out.push({
        x: pos.x + v * Math.cos(heading) * t,
        y: pos.y + v * Math.sin(heading) * t,
        z: pos.z ?? 0,
        heading,
        speed: v,
        relativeTime: t,
      });
    }
    return out;
  }
  const v = speed;
  const out: ObstacleTrajPoint[] = [];
  let t = 0;
  let cx = pos.x;
  let cy = pos.y;
  const p0 = { x: cx, y: cy, z: pos.z ?? 0 };
  out.push({ x: p0.x, y: p0.y, z: p0.z, heading, speed: v, relativeTime: 0 });
  let start = 0;
  let best = Number.POSITIVE_INFINITY;
  for (let i = 0; i < wps.length; i++) {
    const d = Math.hypot(wps[i].position.x - cx, wps[i].position.y - cy);
    if (d < best) {
      best = d;
      start = i;
    }
  }
  for (let i = start; i < wps.length; i++) {
    const w = wps[i].position;
    const dist = Math.hypot(w.x - cx, w.y - cy);
    if (dist < 1e-3) continue;
    t += dist / v;
    const h = Math.atan2(w.y - cy, w.x - cx);
    const enu = w;
    out.push({
      x: enu.x,
      y: enu.y,
      z: enu.z,
      heading: h,
      speed: v,
      relativeTime: t,
    });
    cx = w.x;
    cy = w.y;
    if (t > 8) break;
  }
  return out;
}
