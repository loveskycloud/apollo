import { v4 as uuid } from 'uuid';
import type { Agent, Scenario, Vec3 } from '../core/types';
import { TEST_ROUTE_1HAOLOU } from './testRoute';

const VEHICLE_SIZE = { x: 1, y: 0.5, z: 0.8 };

export function createEgo(position?: Vec3): Agent {
  const size = { ...VEHICLE_SIZE };
  const pos = position
    ? { x: position.x, y: position.y, z: size.z / 2 }
    : { x: 5, y: 0, z: size.z / 2 };
  const routeId = `ego_route_${uuid().slice(0, 4)}`;
  return {
    id: uuid(),
    name: 'Ego Mine Truck',
    type: 'ego',
    position: pos,
    heading: 0,
    speed: 0,
    size,
    color: '#e74c3c',
    activeRouteId: routeId,
    routes: [
      {
        id: routeId,
        name: 'route 1',
        waypoints: [{ id: uuid(), position: { x: pos.x, y: pos.y, z: 0 } }],
      },
    ],
  };
}

/** 默认场景主车 — 坐标均为 Apollo ENU */
export function createDefaultEgo(): Agent {
  const { ego: start, waypoints, routeName } = TEST_ROUTE_1HAOLOU;
  const ego = createEgo(start.position);
  ego.id = 'ego';
  ego.activeRouteId = 'ego_route_1';
  ego.heading = start.heading;
  ego.speed = 0;
  ego.routes = [
    {
      id: 'ego_route_1',
      name: routeName,
      waypoints: waypoints.map((p) => ({
        id: uuid(),
        position: { x: p.x, y: p.y, z: p.z ?? 0 },
      })),
    },
  ];
  return ego;
}

export function createAgent(type: Exclude<Agent['type'], 'ego'>, position?: Vec3): Agent {
  const defaults: Record<Exclude<Agent['type'], 'ego'>, Partial<Agent>> = {
    vehicle: {
      name: 'Mine Truck',
      size: { ...VEHICLE_SIZE },
      color: '#f1c40f',
      speed: 0,
    },
    pedestrian: {
      name: 'Worker',
      size: { x: 0.6, y: 0.6, z: 1.7 },
      color: '#2ecc71',
      speed: 1.2,
    },
    loader: {
      name: 'Loader',
      size: { ...VEHICLE_SIZE },
      color: '#e67e22',
      speed: 0,
    },
    static: {
      name: 'Static Obstacle',
      size: { x: 1, y: 0.5, z: 0.8 },
      color: '#95a5a6',
      speed: 0,
    },
  };

  const base = defaults[type];
  const id = uuid();
  return {
    id,
    name: `${base.name} ${id.slice(0, 4)}`,
    type,
    position: position ?? { x: 30, y: 6, z: (base.size as Vec3).z / 2 },
    heading: Math.PI,
    speed: base.speed ?? 0,
    size: base.size as Vec3,
    color: base.color as string,
    enabled: true,
    routes: [],
  };
}

export function createDefaultScenario(): Scenario {
  const ego = createDefaultEgo();

  return {
    id: uuid(),
    name: '默认场景',
    description: '1haolou test route: Lane_1 → Lane_9 + PnC modules',
    duration: 60,
    agents: [ego],
    triggers: [],
    mapId: '1haolou_202608241047qh',
    simConfig: {
      planning: true,
      control: true,
      prediction: true,
      routing: true,
    },
  };
}
