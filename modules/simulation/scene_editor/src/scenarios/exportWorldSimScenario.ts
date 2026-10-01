import type { Agent, Scenario, Trigger } from '../core/types';

const AGENT_TYPE_PROTO: Record<string, string> = {
  ego: 'AGENT_TYPE_EGO',
  vehicle: 'AGENT_TYPE_VEHICLE',
  pedestrian: 'AGENT_TYPE_PEDESTRIAN',
  loader: 'AGENT_TYPE_LOADER',
  static: 'AGENT_TYPE_STATIC',
};

const TRIGGER_TYPE_PROTO: Record<string, string> = {
  time: 'TRIGGER_TYPE_TIME',
  location: 'TRIGGER_TYPE_LOCATION',
  agent_distance: 'TRIGGER_TYPE_AGENT_DISTANCE',
  speed: 'TRIGGER_TYPE_SPEED',
  behavior: 'TRIGGER_TYPE_BEHAVIOR',
};

const ACTION_KIND_PROTO: Record<string, string> = {
  set_speed: 'ACTION_SET_SPEED',
  start_route: 'ACTION_START_ROUTE',
  stop: 'ACTION_STOP',
  switch_route: 'ACTION_SWITCH_ROUTE',
  enable: 'ACTION_ENABLE',
  disable: 'ACTION_DISABLE',
};

function protoEnum(mapping: Record<string, string>, value: string): string {
  if (!Object.hasOwn(mapping, value)) throw new Error(`不支持的场景类型：${value}`);
  return mapping[value];
}

function mapRoutes(agent: Agent) {
  const defaultPathType = agent.type === 'pedestrian' ? 'bezier' : 'polyline';
  return agent.routes.map((r) => ({
    id: r.id,
    name: r.name,
    pathType: r.pathType ?? defaultPathType,
    waypoints: r.waypoints.map((w) => {
      const out: Record<string, unknown> = {
        id: w.id,
        position: { x: w.position.x, y: w.position.y, z: w.position.z ?? 0 },
        speed: w.speed ?? agent.speed,
      };
      // Absent heading means unconstrained routing, not an east-facing waypoint.
      if (w.heading !== undefined) out.heading = w.heading;
      if (w.handleIn) {
        out.handleIn = {
          x: w.handleIn.x,
          y: w.handleIn.y,
          z: w.handleIn.z ?? 0,
        };
      }
      if (w.handleOut) {
        out.handleOut = {
          x: w.handleOut.x,
          y: w.handleOut.y,
          z: w.handleOut.z ?? 0,
        };
      }
      return out;
    }),
  }));
}

function mapTrigger(t: Trigger): Record<string, unknown> {
  const base: Record<string, unknown> = {
    id: t.id,
    name: t.name,
    type: protoEnum(TRIGGER_TYPE_PROTO, t.type),
    actions: t.actions.map((a) => {
      const out: Record<string, unknown> = {
        kind: protoEnum(ACTION_KIND_PROTO, a.kind),
        targetAgentId: a.targetAgentId,
      };
      if (a.kind === 'set_speed') out.speed = a.speed;
      if (a.kind === 'start_route' || a.kind === 'switch_route') {
        out.routeId = a.routeId;
      }
      return out;
    }),
  };
  switch (t.type) {
    case 'time':
      base.time = t.time;
      break;
    case 'location':
      base.center = t.center;
      base.size = t.size;
      base.heading = t.heading;
      base.radius = t.radius;
      base.targetAgentId = t.targetAgentId;
      break;
    case 'agent_distance':
      base.agentAId = t.agentAId;
      base.agentBId = t.agentBId;
      base.distance = t.distance;
      base.compare = t.compare;
      break;
    case 'speed':
      base.targetAgentId = t.targetAgentId;
      base.speed = t.speed;
      base.compare = t.compare;
      break;
    case 'behavior':
      base.sourceAgentId = t.sourceAgentId;
      base.event = t.event;
      break;
  }
  return base;
}

/**
 * Convert editor Scenario → WorldSim Scenario proto JSON (protobuf JSON mapping).
 * Ego is split into `ego`; non-ego agents keep speed for backend Agent::Tick.
 */
export function buildWorldSimScenarioPayload(scenario: Scenario): Record<string, unknown> {
  if (scenario.agents.filter((a) => a.type === 'ego').length !== 1) {
    throw new Error('WorldSim 场景必须包含且只包含一个主车（ego）');
  }
  if (!Number.isFinite(scenario.duration) || scenario.duration <= 0) {
    throw new Error('场景时长必须为正数');
  }
  const ids = new Set<string>();
  for (const agent of scenario.agents) {
    if (!agent.id || ids.has(agent.id)) throw new Error(`Agent ID 为空或重复：${agent.id}`);
    ids.add(agent.id);
    if (agent.activeRouteId && !agent.routes.some((r) => r.id === agent.activeRouteId)) {
      throw new Error(`Agent ${agent.id} 的当前路径不存在：${agent.activeRouteId}`);
    }
  }
  const ego = scenario.agents.find((a) => a.type === 'ego');
  const agents = scenario.agents
    .filter((a) => a.type !== 'ego')
    .map((a) => ({
      id: a.id,
      name: a.name,
      type: protoEnum(AGENT_TYPE_PROTO, a.type),
      position: { x: a.position.x, y: a.position.y, z: a.position.z ?? 0 },
      heading: a.heading,
      speed: a.speed,
      size: { x: a.size.x, y: a.size.y, z: a.size.z },
      color: a.color,
      enabled: a.enabled !== false,
      activeRouteId: a.activeRouteId ?? '',
      routes: mapRoutes(a),
    }));

  const payload: Record<string, unknown> = {
    id: scenario.id,
    name: scenario.name,
    description: scenario.description ?? '',
    duration: scenario.duration,
    mapId: scenario.mapId,
    simConfig: scenario.simConfig ?? {
      planning: true,
      control: false,
      prediction: true,
      routing: true,
    },
    agents,
    triggers: scenario.triggers.map(mapTrigger),
  };

  if (ego) {
    payload.ego = {
      id: ego.id,
      name: ego.name,
      position: { x: ego.position.x, y: ego.position.y, z: ego.position.z ?? 0 },
      heading: ego.heading,
      size: { x: ego.size.x, y: ego.size.y, z: ego.size.z },
      activeRouteId: ego.activeRouteId ?? '',
      routes: mapRoutes(ego),
      vehicleProfile: '',
    };
  }

  // JSON.stringify otherwise silently replaces NaN/Infinity with null.
  JSON.stringify(payload, (key, value) => {
    if (typeof value === 'number' && !Number.isFinite(value)) {
      throw new Error(`场景字段不是有限数值：${key}`);
    }
    return value;
  });
  return payload;
}
