import type {
  Agent,
  BehaviorAction,
  RuntimeAgentState,
  Scenario,
  Trigger,
  Vec3,
} from './types';
import { distance2D } from '../map/types';

function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

function moveAlongRoute(
  agent: Agent,
  runtime: RuntimeAgentState,
  dt: number,
): RuntimeAgentState {
  const enabled = runtime.enabled ?? agent.enabled !== false;
  if (
    !enabled ||
    !runtime.moving ||
    runtime.speed <= 1e-3 ||
    agent.type === 'static'
  ) {
    return runtime;
  }

  const route = agent.routes.find((r) => r.id === agent.activeRouteId);
  // No path (or single waypoint): cruise along current heading at speed.
  if (!route || route.waypoints.length < 2) {
    const step = runtime.speed * dt;
    return {
      ...runtime,
      position: {
        x: runtime.position.x + step * Math.cos(runtime.heading),
        y: runtime.position.y + step * Math.sin(runtime.heading),
        z: agent.size.z / 2,
      },
    };
  }

  let remaining = Math.max(runtime.speed, 0.01) * dt;
  let progress = runtime.routeProgress;
  let position = { ...runtime.position };
  let heading = runtime.heading;

  while (remaining > 0 && progress < route.waypoints.length - 1) {
    const i = Math.floor(progress);
    const localT = progress - i;
    const a = route.waypoints[i].position;
    const b = route.waypoints[i + 1].position;
    const segLen = distance2D(a, b) || 0.001;
    const distLeftOnSeg = (1 - localT) * segLen;

    if (remaining < distLeftOnSeg) {
      const t = localT + remaining / segLen;
      position = {
        x: lerp(a.x, b.x, t),
        y: lerp(a.y, b.y, t),
        z: agent.size.z / 2,
      };
      heading = Math.atan2(b.y - a.y, b.x - a.x);
      progress = i + t;
      remaining = 0;
    } else {
      remaining -= distLeftOnSeg;
      progress = i + 1;
      position = { x: b.x, y: b.y, z: agent.size.z / 2 };
      heading = Math.atan2(b.y - a.y, b.x - a.x);
    }
  }

  const arrived = progress >= route.waypoints.length - 1;
  return {
    ...runtime,
    position,
    heading,
    routeProgress: Math.min(progress, route.waypoints.length - 1),
    moving: arrived ? false : runtime.moving,
    speed: arrived ? 0 : runtime.speed,
  };
}

function applyAction(
  action: BehaviorAction,
  runtime: Record<string, RuntimeAgentState>,
  agents: Agent[],
): Record<string, RuntimeAgentState> {
  const next = { ...runtime };
  const target = next[action.targetAgentId];
  if (!target) return next;

  switch (action.kind) {
    case 'set_speed':
      next[action.targetAgentId] = {
        ...target,
        speed: action.speed,
        enabled: true,
        moving: action.speed > 1e-3,
      };
      break;
    case 'stop':
      next[action.targetAgentId] = { ...target, moving: false, speed: 0 };
      break;
    case 'enable': {
      const agent = agents.find((a) => a.id === action.targetAgentId);
      const speed =
        target.speed > 1e-3
          ? target.speed
          : Math.max(agent?.speed ?? 0, 0);
      next[action.targetAgentId] = {
        ...target,
        enabled: true,
        speed,
        moving: speed > 1e-3,
      };
      break;
    }
    case 'disable':
      next[action.targetAgentId] = {
        ...target,
        enabled: false,
        moving: false,
      };
      break;
    case 'start_route':
    case 'switch_route': {
      const agent = agents.find((a) => a.id === action.targetAgentId);
      if (!agent) break;
      agent.activeRouteId = action.routeId;
      const speed = Math.max(agent.speed, 0);
      next[action.targetAgentId] = {
        ...target,
        enabled: true,
        moving: speed > 1e-3,
        routeProgress: 0,
        speed,
      };
      break;
    }
  }
  return next;
}

function evaluateTrigger(
  trigger: Trigger,
  time: number,
  agents: Agent[],
  runtime: Record<string, RuntimeAgentState>,
): boolean {
  if (trigger.fired) return false;

  switch (trigger.type) {
    case 'time':
      return time >= trigger.time;
    case 'location': {
      const rt = runtime[trigger.targetAgentId];
      if (!rt) return false;
      const size = trigger.size;
      if (size && size.x > 0 && size.y > 0) {
        const dx = rt.position.x - trigger.center.x;
        const dy = rt.position.y - trigger.center.y;
        const c = Math.cos(-(trigger.heading ?? 0));
        const s = Math.sin(-(trigger.heading ?? 0));
        const localX = dx * c - dy * s;
        const localY = dx * s + dy * c;
        return Math.abs(localX) <= size.x / 2 && Math.abs(localY) <= size.y / 2;
      }
      return distance2D(rt.position, trigger.center) <= (trigger.radius ?? 6);
    }
    case 'agent_distance': {
      const a = runtime[trigger.agentAId];
      const b = runtime[trigger.agentBId];
      if (!a || !b) return false;
      const d = distance2D(a.position, b.position);
      return trigger.compare === 'less' ? d < trigger.distance : d > trigger.distance;
    }
    case 'speed': {
      const rt = runtime[trigger.targetAgentId];
      if (!rt) return false;
      return trigger.compare === 'less'
        ? rt.speed < trigger.speed
        : rt.speed > trigger.speed;
    }
    case 'behavior': {
      const agent = agents.find((a) => a.id === trigger.sourceAgentId);
      const rt = runtime[trigger.sourceAgentId];
      if (!agent || !rt) return false;
      const route = agent.routes.find((r) => r.id === agent.activeRouteId);
      if (trigger.event === 'stopped') return !rt.moving;
      if (trigger.event === 'started') return rt.moving;
      if (trigger.event === 'arrived') {
        return Boolean(route && rt.routeProgress >= route.waypoints.length - 1 && !rt.moving);
      }
      return false;
    }
  }
}

export function stepSimulation(
  scenario: Scenario,
  runtime: Record<string, RuntimeAgentState>,
  time: number,
  dt: number,
): {
  runtime: Record<string, RuntimeAgentState>;
  firedTriggerIds: string[];
} {
  let nextRuntime = { ...runtime };
  const firedTriggerIds: string[] = [];

  for (const trigger of scenario.triggers) {
    if (evaluateTrigger(trigger, time, scenario.agents, nextRuntime)) {
      firedTriggerIds.push(trigger.id);
      for (const action of trigger.actions) {
        nextRuntime = applyAction(action, nextRuntime, scenario.agents);
      }
    }
  }

  for (const agent of scenario.agents) {
    const rt = nextRuntime[agent.id];
    if (!rt) continue;
    nextRuntime[agent.id] = moveAlongRoute(agent, rt, dt);
  }

  return { runtime: nextRuntime, firedTriggerIds };
}

export function worldFromNdc(
  ndcX: number,
  ndcY: number,
  camera: { position: Vec3; target: Vec3 },
): Vec3 {
  // Orthographic-like ground pick approximation for top-down interaction.
  const dx = camera.position.x - camera.target.x;
  const span = Math.max(40, Math.hypot(dx, camera.position.y - camera.target.y));
  return {
    x: camera.target.x + ndcX * span * 0.7,
    y: camera.target.y + ndcY * span * 0.7,
    z: 0,
  };
}
