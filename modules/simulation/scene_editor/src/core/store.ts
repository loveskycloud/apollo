import { create } from 'zustand';
import { v4 as uuid } from 'uuid';
import type {
  Agent,
  AgentType,
  HdMap,
  PlaybackState,
  RuntimeAgentState,
  Scenario,
  SelectedRef,
  ToolMode,
  TransformMode,
  Trigger,
  TriggerType,
  Vec3,
} from './types';
import { createAgent, createDefaultScenario, createEgo } from '../scenarios/defaultScenario';
import { loadMineLaneJson } from '../map/loaders/mineLaneJson';
import {
  loadApolloBaseMapFromText,
  parseApolloBaseMapJson,
} from '../map/loaders/apolloBaseMap';
import { loadApolloBaseMapTxt, parseApolloBaseMapText } from '../map/loaders/apolloBaseMapText';
import { nearestLanePoint, registerMapLoader } from '../map/types';
import { routeBetweenPoints } from './routing';
import { nearestLaneHeading } from '../map/laneHeading';
import { expandRouteAlongLanes } from '../map/routeAlongLanes';
import { refreshBezierHandles, sampleBezierPath } from './bezierPath';
import { buildProjectFile, loadBuiltinMap, type ProjectFile } from '../project/project';
import type { SimModulesConfig } from './types';
import { fromRenderCoords, toRenderCoords } from '../apollo/coords';
import { useViewStore } from '../app/viewStore';

/** 场景路点为 Apollo ENU；沿车道展开前转为地图渲染坐标 */
function buildShowRouteWaypoints(
  agent: Agent,
  route: { waypoints: { position: Vec3; heading?: number }[] },
  map: HdMap | null,
): { x: number; y: number; z: number; heading?: number }[] {
  const origin = toRenderCoords({ x: agent.position.x, y: agent.position.y, z: 0 }, map);
  const rest = route.waypoints.map((w) => {
    const p = toRenderCoords(w.position, map);
    return { x: p.x, y: p.y, z: 0, heading: w.heading };
  });
  if (rest.length === 0) return [origin];
  const d0 = Math.hypot(rest[0].x - origin.x, rest[0].y - origin.y);
  if (d0 < 0.2) return rest;
  return [origin, ...rest];
}
registerMapLoader({
  format: 'mine_lane_json',
  load: async () => loadMineLaneJson(),
});

registerMapLoader({
  format: 'apollo_base_map',
  load: async (source) => {
    if (source.endsWith('.txt') || source.includes('base_map.txt')) {
      return loadApolloBaseMapTxt(source, '1haolou Apollo Base Map');
    }
    const res = await fetch(source);
    if (!res.ok) throw new Error(`Failed to load Apollo Base Map: ${source}`);
    const json = await res.json();
    return parseApolloBaseMapJson(json, 'Apollo Base Map');
  },
});

function cloneScenario(scenario: Scenario): Scenario {
  return structuredClone(scenario);
}

function defaultRuntime(agents: Agent[]): Record<string, RuntimeAgentState> {
  const runtime: Record<string, RuntimeAgentState> = {};
  for (const agent of agents) {
    const enabled = agent.enabled !== false;
    runtime[agent.id] = {
      position: { ...agent.position },
      heading: agent.heading,
      speed: agent.speed,
      routeProgress: 0,
      enabled,
      // Disable 时即使有速度也不开跑；static 始终静止。
      moving:
        agent.type === 'static'
          ? false
          : enabled && (agent.speed > 1e-3 || agent.type === 'ego'),
    };
  }
  return runtime;
}

interface ScenarioStore {
  scenario: Scenario;
  map: HdMap | null;
  /** 项目元信息：场景与地图绑定，地图只能在「项目」菜单中新建/打开时确定 */
  projectName: string;
  projectMapSource: string;
  selected: SelectedRef;
  selectedIds: string[];
  transformMode: TransformMode;
  transformActive: boolean;
  toolMode: ToolMode;
  placeType: AgentType | TriggerType | null;
  playback: PlaybackState;
  runtime: Record<string, RuntimeAgentState>;
  editSnapshot: Scenario | null;
  routingStart: Vec3 | null;
  history: Scenario[];
  future: Scenario[];
  mapLayers: { lanes: boolean; boundaries: boolean; nodes: boolean };
  rendererBackend: 'webgpu' | 'webgl' | 'unknown';

  init: () => Promise<void>;
  select: (selected: SelectedRef, additive?: boolean) => void;
  setSelectedIds: (ids: string[]) => void;
  setTransformMode: (mode: TransformMode) => void;
  clearTransform: () => void;
  setToolMode: (mode: ToolMode, placeType?: ScenarioStore['placeType']) => void;
  setRendererBackend: (backend: ScenarioStore['rendererBackend']) => void;
  pushHistory: () => void;
  undo: () => void;
  redo: () => void;

  addAgent: (type: AgentType, position?: Vec3) => boolean;
  updateAgent: (id: string, patch: Partial<Agent>) => void;
  moveAgents: (ids: string[], delta: Vec3, options?: { moveRoutes?: boolean }) => void;
  setAgentsPositions: (
    positions: Record<string, Vec3>,
    options?: { moveRoutes?: boolean },
  ) => void;
  rotateAgents: (ids: string[], deltaHeading: number) => void;
  scaleAgents: (ids: string[], factor: number) => void;
  setAgentsSize: (sizes: Record<string, Vec3>) => void;
  alignAgentToLane: (id: string) => boolean;
  deleteSelected: () => void;
  deleteRoute: (agentId: string, routeId: string) => void;

  addTrigger: (type: TriggerType, position?: Vec3) => void;
  updateTrigger: (id: string, patch: Partial<Trigger>) => void;
  /** 下一点击 Agent 时，把该 Agent 绑到指定 Trigger 的检测对象 */
  pendingBindTriggerId: string | null;
  armBindTriggerTarget: (triggerId: string | null) => void;
  /** @returns 是否成功绑定 */
  consumeBindTriggerTarget: (agentId: string) => boolean;

  addRoute: (agentId: string) => void;
  addWaypoint: (agentId: string, routeId: string, position: Vec3, heading?: number) => void;
  insertWaypoint: (
    agentId: string,
    routeId: string,
    afterIndex: number,
    position?: Vec3,
  ) => void;
  updateWaypoint: (
    agentId: string,
    routeId: string,
    waypointId: string,
    position: Vec3,
  ) => void;
  /** 更新行人贝塞尔控制柄（绝对 ENU） */
  updateWaypointHandle: (
    agentId: string,
    routeId: string,
    waypointId: string,
    which: 'in' | 'out',
    position: Vec3,
  ) => void;
  deleteWaypoint: (agentId: string, routeId: string, waypointId: string) => void;
  /** 选中 Route 进入编辑；不改写仿真初始 activeRouteId */
  setActiveRoute: (agentId: string, routeId: string) => void;
  /** 校验并沿车道展开当前 Route；返回路径长度（米），失败返回 null */
  showRoute: (agentId: string, routeId: string) => number | null;
  /**
   * 将 Route 全部路点投影到最近车道中心线并回写坐标；
   * 返回成功吸附点数，无地图/无路点/全部失败返回 null
   */
  snapRouteToCenterline: (agentId: string, routeId: string) => number | null;

  setRoutingStart: (point: Vec3 | null) => void;
  applyRouting: (agentId: string, start: Vec3, end: Vec3) => void;
  /** 更新 PnC 模块勾选（仅记录，启动仿真时才拉起），随场景保存 */
  updateSimModules: (modules: SimModulesConfig) => void;

  play: () => void;
  pause: () => void;
  stop: () => void;
  seek: (time: number) => void;
  setPlaybackSpeed: (speed: number) => void;
  tickPlayback: (dt: number) => void;
  setRuntimeAgent: (id: string, patch: Partial<RuntimeAgentState>) => void;
  markTriggerFired: (id: string) => void;

  importScenario: (json: string) => void;
  importMap: (text: string) => Promise<void>;
  exportScenario: () => string;

  /** 项目：新建（空白场景 + 指定地图） */
  newProject: (name: string, map: HdMap, mapSource: string) => void;
  /** 项目：打开（地图由调用方解析后传入） */
  openProjectFile: (project: ProjectFile, map: HdMap) => void;
  /** 项目：导出为项目文件（保存/另存为共用） */
  exportProjectFile: () => ProjectFile;
  setProjectName: (name: string) => void;

  toggleMapLayer: (layer: keyof ScenarioStore['mapLayers']) => void;
}

function createTrigger(
  type: TriggerType,
  agents: Agent[],
  position?: Vec3,
  /** 当前选中的 Agent：作为检测/绑定对象（不限主车） */
  preferredTargetId?: string,
): Trigger {
  const ego = agents.find((a) => a.type === 'ego') ?? agents[0];
  if (!ego) {
    throw new Error('场景中没有可用 Agent，无法创建触发器');
  }
  const preferred =
    preferredTargetId && agents.some((a) => a.id === preferredTargetId)
      ? preferredTargetId
      : undefined;
  const detectId = preferred ?? ego.id;
  const other =
    agents.find((a) => a.id !== detectId) ??
    agents.find((a) => a.id !== ego.id) ??
    ego;
  const id = uuid();
  const center = position ?? { x: 60, y: 0, z: 0 };

  switch (type) {
    case 'time':
      return {
        id,
        name: 'Time Trigger',
        type: 'time',
        time: 5,
        actions: [{ kind: 'set_speed', targetAgentId: detectId, speed: 2 }],
      };
    case 'location': {
      const ped = agents.find((a) => a.type === 'pedestrian');
      const actionTargetId =
        ped && ped.id !== detectId
          ? ped.id
          : other.id !== detectId
            ? other.id
            : detectId;
      return {
        id,
        name: 'Location Trigger',
        type: 'location',
        center,
        size: { x: 10, y: 4.5, z: 0.5 },
        heading: 0,
        // 绑定检测对象：优先当前选中 Agent，否则默认主车（可随时改）
        targetAgentId: detectId,
        actions: ped
          ? [{ kind: 'enable', targetAgentId: ped.id }]
          : [{ kind: 'enable', targetAgentId: actionTargetId }],
      };
    }

    case 'agent_distance':
      return {
        id,
        name: 'Agent Distance Trigger',
        type: 'agent_distance',
        agentAId: detectId,
        agentBId: other.id,
        distance: 15,
        compare: 'less',
        actions: [{ kind: 'stop', targetAgentId: detectId }],
      };
    case 'speed':
      return {
        id,
        name: 'Speed Trigger',
        type: 'speed',
        targetAgentId: detectId,
        speed: 3,
        compare: 'greater',
        actions: [{ kind: 'set_speed', targetAgentId: detectId, speed: 2 }],
      };
    case 'behavior':
      return {
        id,
        name: 'Behavior Trigger',
        type: 'behavior',
        sourceAgentId: detectId,
        event: 'arrived',
        actions: [{ kind: 'stop', targetAgentId: detectId }],
      };
  }
}

export const useScenarioStore = create<ScenarioStore>((set, get) => ({
  scenario: createDefaultScenario(),
  map: null,
  projectName: '默认项目',
  projectMapSource: 'builtin:1haolou',
  selected: { kind: 'agent', id: 'ego' },
  selectedIds: ['ego'],
  transformMode: 'translate',
  transformActive: false,
  toolMode: 'select',
  placeType: null,
  pendingBindTriggerId: null,
  playback: { playing: false, time: 0, speed: 1, duration: 60 },
  runtime: defaultRuntime(createDefaultScenario().agents),
  editSnapshot: null,
  routingStart: null,
  history: [],
  future: [],
  mapLayers: { lanes: true, boundaries: true, nodes: true },
  rendererBackend: 'unknown',

  init: async () => {
    const map = await loadBuiltinMap('builtin:1haolou_202608241047qh');
    const scenario = get().scenario;
    const spawn = map.lanes[0]?.centerline[0] ?? { x: 0, y: 0, z: 0 };
    const agents = scenario.agents.map((agent) => {
      if (agent.type === 'ego') return agent;
      const lane = map.lanes[Math.min(3, map.lanes.length - 1)];
      const p = lane?.centerline[Math.floor((lane.centerline.length - 1) / 2)] ?? spawn;
      const base = fromRenderCoords({ x: p.x, y: p.y, z: agent.size.z / 2 }, map);
      return {
        ...agent,
        position: base,
        routes: agent.routes.map((route) => ({
          ...route,
          waypoints: route.waypoints.map((w, idx) => ({
            ...w,
            position: fromRenderCoords(
              { x: p.x + idx * 8, y: p.y, z: 0 },
              map,
            ),
          })),
        })),
      };
    });
    const nextScenario = {
      ...scenario,
      agents,
      mapId: map.id,
      simConfig: { planning: true, control: true, prediction: true, routing: true },
    };
    set({
      map,
      scenario: nextScenario,
      projectMapSource: 'builtin:1haolou_202608241047qh',
      playback: { ...get().playback, duration: scenario.duration },
      runtime: defaultRuntime(agents),
    });
  },

  select: (selected, additive = false) => {
    if (!selected) {
      set({ selected: null, selectedIds: [], transformActive: false });
      return;
    }
    // 点选可编辑对象时切到右侧属性面板
    useViewStore.getState().setRightTab('attrs');
    if (selected.kind === 'agent') {
      if (additive) {
        const ids = new Set(get().selectedIds);
        if (ids.has(selected.id)) ids.delete(selected.id);
        else ids.add(selected.id);
        const next = [...ids];
        set({
          selectedIds: next,
          selected: next.length ? { kind: 'agent', id: next[next.length - 1] } : null,
          // 选择集变化后必须重新按 E/R/S 才可变换
          transformActive: false,
        });
      } else {
        const prev = get().selectedIds;
        const sameOnly =
          prev.length === 1 && prev[0] === selected.id;
        set({
          selected,
          selectedIds: [selected.id],
          // 点选其他物体时退出变换；重复点同一物体可保留
          transformActive: sameOnly ? get().transformActive : false,
        });
      }
      return;
    }
    if (selected.kind === 'route') {
      set({
        selected,
        selectedIds: [selected.agentId],
        transformActive: false,
      });
      return;
    }
    set({ selected, selectedIds: [], transformActive: false });
  },

  setSelectedIds: (ids) => {
    const prev = get().selectedIds;
    const same =
      prev.length === ids.length && prev.every((id, i) => id === ids[i]);
    if (ids.length > 0) {
      useViewStore.getState().setRightTab('attrs');
    }
    set({
      selectedIds: ids,
      selected: ids.length ? { kind: 'agent', id: ids[ids.length - 1] } : null,
      // 框选/切换选中后退出变换，避免带到新物体上
      transformActive: same ? get().transformActive : false,
    });
  },

  setTransformMode: (mode) =>
    set({
      transformMode: mode,
      transformActive: true,
      toolMode: 'select',
      placeType: null,
    }),

  clearTransform: () => set({ transformActive: false }),

  setToolMode: (mode, placeType = null) =>
    set({
      toolMode: mode,
      placeType,
      // 进入选择/放置等其他工具时关闭变换，避免误拖
      transformActive: false,
    }),

  setRendererBackend: (backend) => set({ rendererBackend: backend }),

  pushHistory: () => {
    const { scenario, history } = get();
    set({
      history: [...history.slice(-49), cloneScenario(scenario)],
      future: [],
    });
  },

  undo: () => {
    const { history, scenario, future } = get();
    if (history.length === 0) return;
    const prev = history[history.length - 1];
    set({
      scenario: prev,
      history: history.slice(0, -1),
      future: [cloneScenario(scenario), ...future],
      runtime: defaultRuntime(prev.agents),
    });
  },

  redo: () => {
    const { history, scenario, future } = get();
    if (future.length === 0) return;
    const next = future[0];
    set({
      scenario: next,
      history: [...history, cloneScenario(scenario)],
      future: future.slice(1),
      runtime: defaultRuntime(next.agents),
    });
  },

  addAgent: (type, position) => {
    if (type === 'ego' && get().scenario.agents.some((a) => a.type === 'ego')) {
      return false;
    }
    get().pushHistory();
    const agent = type === 'ego' ? createEgo(position) : createAgent(type, position);
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: [...state.scenario.agents, agent],
      },
      runtime: {
        ...state.runtime,
        [agent.id]: {
          position: { ...agent.position },
          heading: agent.heading,
          speed: agent.speed,
          routeProgress: 0,
          enabled: agent.enabled !== false,
          moving:
            agent.type === 'static'
              ? false
              : agent.enabled !== false &&
                (agent.speed > 1e-3 || agent.type === 'ego'),
        },
      },
      selected: { kind: 'agent', id: agent.id },
      selectedIds: [agent.id],
      toolMode: 'select',
      placeType: null,
    }));
    return true;
  },

  updateAgent: (id, patch) => {
    set((state) => {
      const prev = state.scenario.agents.find((a) => a.id === id);
      const nextEnabled =
        patch.enabled !== undefined ? patch.enabled !== false : undefined;
      const nextSpeed = patch.speed != null ? patch.speed : undefined;
      return {
        scenario: {
          ...state.scenario,
          agents: state.scenario.agents.map((a) =>
            a.id === id ? { ...a, ...patch } : a,
          ),
        },
        runtime: state.runtime[id]
          ? {
              ...state.runtime,
              [id]: {
                ...state.runtime[id],
                ...(patch.position ? { position: patch.position } : {}),
                ...(patch.heading != null ? { heading: patch.heading } : {}),
                ...(nextEnabled !== undefined ? { enabled: nextEnabled } : {}),
                ...(nextSpeed != null
                  ? {
                      speed: nextSpeed,
                      moving:
                        prev?.type === 'static'
                          ? false
                          : (nextEnabled ?? state.runtime[id].enabled !== false) &&
                            nextSpeed > 1e-3,
                    }
                  : nextEnabled !== undefined
                    ? {
                        moving:
                          prev?.type === 'static'
                            ? false
                            : nextEnabled && state.runtime[id].speed > 1e-3,
                      }
                    : {}),
              },
            }
          : state.runtime,
      };
    });
  },

  moveAgents: (ids, delta, options) => {
    const moveRoutes = options?.moveRoutes ?? true;
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((agent) => {
          if (!ids.includes(agent.id)) return agent;
          return {
            ...agent,
            position: {
              x: agent.position.x + delta.x,
              y: agent.position.y + delta.y,
              z: agent.position.z + (delta.z ?? 0),
            },
            routes: moveRoutes
              ? agent.routes.map((route) => ({
                  ...route,
                  waypoints: route.waypoints.map((w) => ({
                    ...w,
                    position: {
                      x: w.position.x + delta.x,
                      y: w.position.y + delta.y,
                      z: w.position.z,
                    },
                  })),
                }))
              : agent.routes,
          };
        }),
      },
      runtime: Object.fromEntries(
        Object.entries(state.runtime).map(([id, rt]) =>
          ids.includes(id)
            ? [
                id,
                {
                  ...rt,
                  position: {
                    x: rt.position.x + delta.x,
                    y: rt.position.y + delta.y,
                    z: rt.position.z + (delta.z ?? 0),
                  },
                },
              ]
            : [id, rt],
        ),
      ),
    }));
  },

  setAgentsPositions: (positions, options) => {
    const moveRoutes = options?.moveRoutes ?? true;
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((agent) => {
          const next = positions[agent.id];
          if (!next) return agent;
          const dx = next.x - agent.position.x;
          const dy = next.y - agent.position.y;
          return {
            ...agent,
            position: { ...next },
            routes: moveRoutes
              ? agent.routes.map((route) => ({
                  ...route,
                  waypoints: route.waypoints.map((w) => ({
                    ...w,
                    position: {
                      x: w.position.x + dx,
                      y: w.position.y + dy,
                      z: w.position.z,
                    },
                  })),
                }))
              : agent.routes,
          };
        }),
      },
      runtime: Object.fromEntries(
        Object.entries(state.runtime).map(([id, rt]) => {
          const next = positions[id];
          if (!next) return [id, rt];
          return [id, { ...rt, position: { x: next.x, y: next.y, z: next.z } }];
        }),
      ),
    }));
  },

  rotateAgents: (ids, deltaHeading) => {
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((agent) =>
          ids.includes(agent.id)
            ? { ...agent, heading: agent.heading + deltaHeading }
            : agent,
        ),
      },
      runtime: Object.fromEntries(
        Object.entries(state.runtime).map(([id, rt]) =>
          ids.includes(id)
            ? [id, { ...rt, heading: rt.heading + deltaHeading }]
            : [id, rt],
        ),
      ),
    }));
  },

  alignAgentToLane: (id) => {
    const { map, scenario, runtime } = get();
    if (!map) return false;
    const agent = scenario.agents.find((a) => a.id === id);
    if (!agent) return false;
    const stored = runtime[id]?.position ?? agent.position;
    const pos = toRenderCoords(stored, map);
    const current = runtime[id]?.heading ?? agent.heading;
    const heading = nearestLaneHeading(map, pos.x, pos.y, current);
    if (heading == null) return false;
    get().pushHistory();
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) =>
          a.id === id ? { ...a, heading } : a,
        ),
      },
      runtime: {
        ...state.runtime,
        [id]: state.runtime[id]
          ? { ...state.runtime[id], heading }
          : state.runtime[id],
      },
    }));
    return true;
  },

  scaleAgents: (ids, factor) => {
    const f = Math.max(0.05, Math.min(20, factor));
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((agent) =>
          ids.includes(agent.id)
            ? {
                ...agent,
                size: {
                  x: Math.max(0.1, agent.size.x * f),
                  y: Math.max(0.1, agent.size.y * f),
                  z: Math.max(0.1, agent.size.z * f),
                },
                position: {
                  ...agent.position,
                  z: Math.max(0.05, (agent.size.z * f) / 2),
                },
              }
            : agent,
        ),
      },
      runtime: Object.fromEntries(
        Object.entries(state.runtime).map(([id, rt]) => {
          if (!ids.includes(id)) return [id, rt];
          const agent = state.scenario.agents.find((a) => a.id === id);
          const z = Math.max(0.05, ((agent?.size.z ?? rt.position.z * 2) * f) / 2);
          return [id, { ...rt, position: { ...rt.position, z } }];
        }),
      ),
    }));
  },

  setAgentsSize: (sizes) => {
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((agent) => {
          const size = sizes[agent.id];
          if (!size) return agent;
          const next = {
            x: Math.max(0.01, size.x),
            y: Math.max(0.01, size.y),
            z: Math.max(0.01, size.z),
          };
          return {
            ...agent,
            size: next,
            position: { ...agent.position, z: next.z / 2 },
          };
        }),
      },
      runtime: Object.fromEntries(
        Object.entries(state.runtime).map(([id, rt]) => {
          const size = sizes[id];
          if (!size) return [id, rt];
          return [id, { ...rt, position: { ...rt.position, z: Math.max(0.05, size.z / 2) } }];
        }),
      ),
    }));
  },

  deleteSelected: () => {
    const { selected, selectedIds, scenario } = get();

    // 删除选中轨迹
    if (selected?.kind === 'route') {
      get().pushHistory();
      const { agentId, routeId } = selected;
      set((state) => {
        const agents = state.scenario.agents.map((a) => {
          if (a.id !== agentId) return a;
          const routes = a.routes.filter((r) => r.id !== routeId);
          return {
            ...a,
            routes,
            activeRouteId:
              a.activeRouteId === routeId ? routes[0]?.id : a.activeRouteId,
          };
        });
        return {
          scenario: { ...state.scenario, agents },
          selected: { kind: 'agent', id: agentId },
          selectedIds: [agentId],
        };
      });
      return;
    }

    if (selectedIds.length > 0) {
      const removable = selectedIds.filter((id) =>
        scenario.agents.some((a) => a.id === id),
      );
      if (removable.length === 0) return;

      get().pushHistory();
      set((state) => {
        const runtime = { ...state.runtime };
        removable.forEach((id) => delete runtime[id]);
        const agents = state.scenario.agents.filter((a) => !removable.includes(a.id));
        const nextId =
          agents.find((a) => a.type === 'ego')?.id ?? agents[0]?.id ?? null;
        return {
          scenario: {
            ...state.scenario,
            agents,
            triggers: state.scenario.triggers.filter((t) => {
              if (t.type === 'agent_distance') {
                return !removable.includes(t.agentAId) && !removable.includes(t.agentBId);
              }
              if ('targetAgentId' in t) return !removable.includes(t.targetAgentId);
              if ('sourceAgentId' in t) return !removable.includes(t.sourceAgentId);
              return true;
            }),
          },
          runtime,
          selectedIds: nextId ? [nextId] : [],
          selected: nextId ? { kind: 'agent', id: nextId } : null,
        };
      });
      return;
    }

    if (!selected) return;
    if (selected.kind === 'trigger') {
      get().pushHistory();
      const agents = get().scenario.agents;
      const nextId = agents.find((a) => a.type === 'ego')?.id ?? agents[0]?.id ?? null;
      set((state) => ({
        scenario: {
          ...state.scenario,
          triggers: state.scenario.triggers.filter((t) => t.id !== selected.id),
        },
        selected: nextId ? { kind: 'agent', id: nextId } : null,
        selectedIds: nextId ? [nextId] : [],
      }));
    }
  },

  deleteRoute: (agentId, routeId) => {
    get().pushHistory();
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) => {
          if (a.id !== agentId) return a;
          const routes = a.routes.filter((r) => r.id !== routeId);
          return {
            ...a,
            routes,
            activeRouteId:
              a.activeRouteId === routeId ? routes[0]?.id : a.activeRouteId,
          };
        }),
      },
      selected: { kind: 'agent', id: agentId },
      selectedIds: [agentId],
    }));
  },

  addTrigger: (type, position) => {
    get().pushHistory();
    const state = get();
    const preferred =
      state.selected?.kind === 'agent'
        ? state.selected.id
        : state.selectedIds[0];
    const trigger = createTrigger(
      type,
      state.scenario.agents,
      position,
      preferred,
    );
    set((s) => ({
      scenario: {
        ...s.scenario,
        triggers: [...s.scenario.triggers, trigger],
      },
      selected: { kind: 'trigger', id: trigger.id },
      toolMode: 'select',
      placeType: null,
      pendingBindTriggerId: null,
    }));
  },

  updateTrigger: (id, patch) => {
    set((state) => ({
      scenario: {
        ...state.scenario,
        triggers: state.scenario.triggers.map((t) =>
          t.id === id ? ({ ...t, ...patch } as Trigger) : t,
        ),
      },
    }));
  },

  armBindTriggerTarget: (triggerId) => {
    set({ pendingBindTriggerId: triggerId });
  },

  consumeBindTriggerTarget: (agentId) => {
    const triggerId = get().pendingBindTriggerId;
    if (!triggerId) return false;
    const trigger = get().scenario.triggers.find((t) => t.id === triggerId);
    if (!trigger) {
      set({ pendingBindTriggerId: null });
      return false;
    }
    get().pushHistory();
    if (trigger.type === 'location' || trigger.type === 'speed') {
      get().updateTrigger(triggerId, { targetAgentId: agentId } as Partial<Trigger>);
    } else if (trigger.type === 'behavior') {
      get().updateTrigger(triggerId, { sourceAgentId: agentId } as Partial<Trigger>);
    } else if (trigger.type === 'agent_distance') {
      get().updateTrigger(triggerId, { agentAId: agentId } as Partial<Trigger>);
    } else {
      set({ pendingBindTriggerId: null });
      return false;
    }
    set({
      pendingBindTriggerId: null,
      selected: { kind: 'trigger', id: triggerId },
      selectedIds: [],
    });
    return true;
  },

  addRoute: (agentId) => {
    get().pushHistory();
    const routeId = uuid();
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) => {
          if (a.id !== agentId) return a;
          // 默认起点：首条 = 当前位置；后续 = 上一条 Route 终点
          const prev = a.routes[a.routes.length - 1];
          const startPos =
            prev && prev.waypoints.length > 0
              ? {
                  x: prev.waypoints[prev.waypoints.length - 1].position.x,
                  y: prev.waypoints[prev.waypoints.length - 1].position.y,
                  z: 0,
                }
              : { x: a.position.x, y: a.position.y, z: 0 };
          const pathType = a.type === 'pedestrian' ? 'bezier' : 'polyline';
          // activeRouteId = 仿真初始路径（按添加顺序默认第一条）。
          // 追加 Route 只选中新路径用于编辑，不得改写初始 activeRouteId。
          const keepInitial =
            a.activeRouteId && a.routes.some((r) => r.id === a.activeRouteId)
              ? a.activeRouteId
              : a.routes[0]?.id;
          return {
            ...a,
            activeRouteId: keepInitial ?? routeId,
            routes: [
              ...a.routes,
              {
                id: routeId,
                name: `route ${a.routes.length + 1}`,
                pathType,
                waypoints: [{ id: uuid(), position: startPos }],
              },
            ],
          };
        }),
      },
      selected: { kind: 'route', agentId, routeId },
      selectedIds: [agentId],
      toolMode: 'route_edit',
    }));
  },

  addWaypoint: (agentId, routeId, position, heading) => {
    get().pushHistory();
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) => {
          if (a.id !== agentId) return a;
          return {
            ...a,
            routes: a.routes.map((r) => {
              if (r.id !== routeId) return r;
              const isBezier =
                r.pathType === 'bezier' || a.type === 'pedestrian';
              const nextWps = [
                ...r.waypoints,
                {
                  id: uuid(),
                  position: { ...position, z: 0 },
                  ...(heading != null ? { heading } : {}),
                },
              ];
              return {
                ...r,
                pathType: isBezier ? 'bezier' : r.pathType ?? 'polyline',
                waypoints: isBezier ? refreshBezierHandles(nextWps, false) : nextWps,
              };
            }),
          };
        }),
      },
    }));
  },

  insertWaypoint: (agentId, routeId, afterIndex, position) => {
    get().pushHistory();
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) => {
          if (a.id !== agentId) return a;
          return {
            ...a,
            routes: a.routes.map((r) => {
              if (r.id !== routeId) return r;
              const wps = r.waypoints;
              const i = Math.max(-1, Math.min(afterIndex, wps.length - 1));
              let pos = position;
              if (!pos) {
                if (i < 0) {
                  pos = { x: a.position.x, y: a.position.y, z: 0 };
                } else if (i >= wps.length - 1) {
                  const last = wps[wps.length - 1].position;
                  pos = { x: last.x + 1, y: last.y, z: 0 };
                } else {
                  const aPos = wps[i].position;
                  const bPos = wps[i + 1].position;
                  pos = {
                    x: (aPos.x + bPos.x) / 2,
                    y: (aPos.y + bPos.y) / 2,
                    z: 0,
                  };
                }
              }
              const next = [...wps];
              next.splice(i + 1, 0, { id: uuid(), position: { ...pos, z: 0 } });
              const isBezier =
                r.pathType === 'bezier' || a.type === 'pedestrian';
              return {
                ...r,
                pathType: isBezier ? 'bezier' : r.pathType ?? 'polyline',
                waypoints: isBezier ? refreshBezierHandles(next, false) : next,
              };
            }),
          };
        }),
      },
    }));
  },

  updateWaypoint: (agentId, routeId, waypointId, position) => {
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) =>
          a.id === agentId
            ? {
                ...a,
                routes: a.routes.map((r) =>
                  r.id === routeId
                    ? {
                        ...r,
                        waypoints: r.waypoints.map((w) => {
                          if (w.id !== waypointId) return w;
                          const nx = position.x;
                          const ny = position.y;
                          const dx = nx - w.position.x;
                          const dy = ny - w.position.y;
                          return {
                            ...w,
                            position: { x: nx, y: ny, z: 0 },
                            handleIn: w.handleIn
                              ? {
                                  x: w.handleIn.x + dx,
                                  y: w.handleIn.y + dy,
                                  z: 0,
                                }
                              : w.handleIn,
                            handleOut: w.handleOut
                              ? {
                                  x: w.handleOut.x + dx,
                                  y: w.handleOut.y + dy,
                                  z: 0,
                                }
                              : w.handleOut,
                          };
                        }),
                      }
                    : r,
                ),
              }
            : a,
        ),
      },
    }));
  },

  updateWaypointHandle: (agentId, routeId, waypointId, which, position) => {
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) =>
          a.id === agentId
            ? {
                ...a,
                routes: a.routes.map((r) =>
                  r.id === routeId
                    ? {
                        ...r,
                        pathType: r.pathType ?? 'bezier',
                        waypoints: r.waypoints.map((w) =>
                          w.id === waypointId
                            ? {
                                ...w,
                                ...(which === 'in'
                                  ? {
                                      handleIn: {
                                        x: position.x,
                                        y: position.y,
                                        z: 0,
                                      },
                                    }
                                  : {
                                      handleOut: {
                                        x: position.x,
                                        y: position.y,
                                        z: 0,
                                      },
                                    }),
                              }
                            : w,
                        ),
                      }
                    : r,
                ),
              }
            : a,
        ),
      },
    }));
  },

  deleteWaypoint: (agentId, routeId, waypointId) => {
    get().pushHistory();
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) =>
          a.id === agentId
            ? {
                ...a,
                routes: a.routes.map((r) => {
                  if (r.id !== routeId) return r;
                  const next = r.waypoints.filter((w) => w.id !== waypointId);
                  const isBezier =
                    r.pathType === 'bezier' || a.type === 'pedestrian';
                  return {
                    ...r,
                    waypoints: isBezier
                      ? refreshBezierHandles(next, false)
                      : next,
                  };
                }),
              }
            : a,
        ),
      },
    }));
  },

  /** 选中 Route 进入编辑（不改变仿真初始 activeRouteId）。 */
  setActiveRoute: (agentId, routeId) => {
    set({
      selected: { kind: 'route', agentId, routeId },
      selectedIds: [agentId],
    });
  },

  showRoute: (agentId, routeId) => {
    const { map, scenario } = get();
    const agent = scenario.agents.find((a) => a.id === agentId);
    const route = agent?.routes.find((r) => r.id === routeId);
    if (!agent || !route || route.waypoints.length < 1) return null;
    const isBezier =
      route.pathType === 'bezier' || agent.type === 'pedestrian';
    const path = isBezier
      ? sampleBezierPath(
          route.waypoints.map((w) => ({
            ...w,
            position: toRenderCoords(w.position, map),
            handleIn: w.handleIn
              ? toRenderCoords(w.handleIn, map)
              : undefined,
            handleOut: w.handleOut
              ? toRenderCoords(w.handleOut, map)
              : undefined,
          })),
          0.25,
        )
      : expandRouteAlongLanes(
          map,
          buildShowRouteWaypoints(agent, route, map),
        );
    if (path.length < 2) return null;
    let len = 0;
    for (let i = 1; i < path.length; i += 1) {
      len += Math.hypot(path[i].x - path[i - 1].x, path[i].y - path[i - 1].y);
    }
    // 仅进入编辑选中；Show route 不得改写仿真初始路径
    set({
      selected: { kind: 'route', agentId, routeId },
      selectedIds: [agentId],
    });
    return len;
  },

  snapRouteToCenterline: (agentId, routeId) => {
    const { map, scenario } = get();
    if (!map) return null;
    const agent = scenario.agents.find((a) => a.id === agentId);
    const route = agent?.routes.find((r) => r.id === routeId);
    if (!agent || !route || route.waypoints.length < 1) return null;

    let snapped = 0;
    const nextWaypoints = route.waypoints.map((w) => {
      const renderPos = toRenderCoords(w.position, map);
      const snap = nearestLanePoint(map, renderPos, w.heading);
      if (!snap) return w;
      snapped += 1;
      const position = fromRenderCoords(
        { x: snap.position.x, y: snap.position.y, z: 0 },
        map,
      );
      return { ...w, position, heading: snap.heading };
    });
    if (snapped === 0) return null;

    get().pushHistory();
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) =>
          a.id === agentId
            ? {
                ...a,
                routes: a.routes.map((r) =>
                  r.id === routeId ? { ...r, waypoints: nextWaypoints } : r,
                ),
              }
            : a,
        ),
      },
      selected: { kind: 'route', agentId, routeId },
      selectedIds: [agentId],
    }));
    return snapped;
  },

  setRoutingStart: (point) => set({ routingStart: point }),

  updateSimModules: (modules) =>
    set((state) => ({
      scenario: { ...state.scenario, simConfig: { ...modules } },
    })),

  applyRouting: (agentId, start, end) => {
    const map = get().map;
    if (!map) return;
    const s = toRenderCoords(start, map);
    const e = toRenderCoords(end, map);
    const segHeading = Math.atan2(e.y - s.y, e.x - s.x);
    const result = routeBetweenPoints(map, s, e, {
      startHeading: segHeading,
      endHeading: segHeading,
      maxDetourRatio: 6,
    });
    if (!result) return;
    get().pushHistory();
    const routeId = uuid();
    set((state) => ({
      scenario: {
        ...state.scenario,
        agents: state.scenario.agents.map((a) => {
          if (a.id !== agentId) return a;
          const keepInitial =
            a.activeRouteId && a.routes.some((r) => r.id === a.activeRouteId)
              ? a.activeRouteId
              : a.routes[0]?.id;
          return {
            ...a,
            // 追加 routing 只用于编辑选中；仿真初始路径保持第一条
            activeRouteId: keepInitial ?? routeId,
            routes: [
              ...a.routes,
              {
                id: routeId,
                name: `routing ${a.routes.length + 1}`,
                waypoints: result.waypoints.map((p) => ({
                  id: uuid(),
                  position: fromRenderCoords(p, map),
                  heading: segHeading,
                })),
              },
            ],
          };
        }),
      },
      routingStart: null,
      toolMode: 'select',
      selected: { kind: 'route', agentId, routeId },
      selectedIds: [agentId],
    }));
  },

  play: () => {
    const { playback, scenario, editSnapshot, runtime } = get();
    // Ensure non-ego agents with speed are marked moving when Play starts.
    const nextRuntime = { ...runtime };
    for (const agent of scenario.agents) {
      const rt = nextRuntime[agent.id];
      if (!rt) continue;
      if (agent.type === 'static') {
        nextRuntime[agent.id] = { ...rt, moving: false, speed: 0 };
        continue;
      }
      const speed = rt.speed > 1e-3 ? rt.speed : agent.speed;
      nextRuntime[agent.id] = {
        ...rt,
        speed,
        moving: agent.type === 'ego' ? rt.moving || speed > 1e-3 : speed > 1e-3,
      };
    }
    set({
      editSnapshot: editSnapshot ?? cloneScenario(scenario),
      playback: { ...playback, playing: true },
      runtime: nextRuntime,
      scenario: {
        ...scenario,
        triggers: scenario.triggers.map((t) => ({ ...t, fired: false })),
      },
    });
  },

  pause: () => set((s) => ({ playback: { ...s.playback, playing: false } })),

  stop: () => {
    const { editSnapshot, scenario } = get();
    const restored = editSnapshot ?? scenario;
    set({
      scenario: {
        ...restored,
        triggers: restored.triggers.map((t) => ({ ...t, fired: false })),
      },
      runtime: defaultRuntime(restored.agents),
      playback: {
        playing: false,
        time: 0,
        speed: get().playback.speed,
        duration: restored.duration,
      },
      editSnapshot: null,
    });
  },

  seek: (time) =>
    set((s) => ({
      playback: {
        ...s.playback,
        time: Math.max(0, Math.min(time, s.playback.duration)),
      },
    })),

  setPlaybackSpeed: (speed) =>
    set((s) => ({ playback: { ...s.playback, speed } })),

  tickPlayback: (dt) => {
    const { playback } = get();
    if (!playback.playing) return;
    const next = Math.min(playback.duration, playback.time + dt * playback.speed);
    set({ playback: { ...playback, time: next, playing: next < playback.duration } });
  },

  setRuntimeAgent: (id, patch) =>
    set((state) => ({
      runtime: {
        ...state.runtime,
        [id]: { ...state.runtime[id], ...patch },
      },
    })),

  markTriggerFired: (id) =>
    set((state) => ({
      scenario: {
        ...state.scenario,
        triggers: state.scenario.triggers.map((t) =>
          t.id === id ? { ...t, fired: true } : t,
        ),
      },
    })),

  importScenario: (json) => {
    const parsed = JSON.parse(json) as Scenario;
    get().pushHistory();
    const egoId =
      parsed.agents.find((a) => a.type === 'ego')?.id ?? parsed.agents[0]?.id;
    set({
      scenario: parsed,
      runtime: defaultRuntime(parsed.agents),
      playback: {
        playing: false,
        time: 0,
        speed: 1,
        duration: parsed.duration,
      },
      selected: egoId ? { kind: 'agent', id: egoId } : null,
      selectedIds: egoId ? [egoId] : [],
    });
  },

  importMap: async (text) => {
    const trimmed = text.trim();
    const map = trimmed.startsWith('{')
      ? loadApolloBaseMapFromText(text, 'Imported Apollo Base Map')
      : parseApolloBaseMapText(text, 'Imported Apollo Base Map');
    set((state) => ({
      map,
      scenario: { ...state.scenario, mapId: map.id },
    }));
  },

  exportScenario: () => JSON.stringify(get().scenario, null, 2),

  newProject: (name, map, mapSource) => {
    // 空白项目：不自动放置主车。车道 centerline 是 origin 归一化渲染坐标，
    // 不可直接写入 Agent（场景侧应为 Apollo ENU）；由用户在视口点击放置。
    const scenario: Scenario = {
      id: uuid(),
      name: '空白场景',
      duration: 60,
      agents: [],
      triggers: [],
      mapId: map.id,
      simConfig: { planning: true, control: true, prediction: true, routing: true },
    };
    set({
      map,
      scenario,
      projectName: name,
      projectMapSource: mapSource,
      runtime: defaultRuntime(scenario.agents),
      playback: { playing: false, time: 0, speed: 1, duration: scenario.duration },
      editSnapshot: null,
      history: [],
      future: [],
      selected: null,
      selectedIds: [],
      routingStart: null,
      toolMode: 'select',
      placeType: null,
    });
  },

  openProjectFile: (project, map) => {
    const scenario = structuredClone(project.scenario);
    scenario.mapId = map.id;
    scenario.simConfig = scenario.simConfig ?? {
      planning: true,
      control: false,
      prediction: false,
      routing: true,
    };
    const egoId =
      scenario.agents.find((a) => a.type === 'ego')?.id ??
      scenario.agents[0]?.id ??
      null;
    set({
      map,
      scenario,
      projectName: project.name,
      projectMapSource: project.mapSource,
      runtime: defaultRuntime(scenario.agents),
      playback: {
        playing: false,
        time: 0,
        speed: 1,
        duration: scenario.duration || 60,
      },
      editSnapshot: null,
      history: [],
      future: [],
      selected: egoId ? { kind: 'agent', id: egoId } : null,
      selectedIds: egoId ? [egoId] : [],
      routingStart: null,
      toolMode: 'select',
    });
  },

  exportProjectFile: () => {
    const { scenario, map, projectName, projectMapSource } = get();
    const fallbackMap: HdMap =
      map ?? {
        id: 'empty',
        name: '无地图',
        format: 'mine_lane_json',
        bounds: { min: { x: 0, y: 0, z: 0 }, max: { x: 100, y: 100, z: 0 } },
        lanes: [],
        nodes: [],
      };
    return buildProjectFile(projectName, fallbackMap, scenario, projectMapSource);
  },

  setProjectName: (name) => set({ projectName: name }),

  toggleMapLayer: (layer) =>
    set((state) => ({
      mapLayers: { ...state.mapLayers, [layer]: !state.mapLayers[layer] },
    })),
}));
