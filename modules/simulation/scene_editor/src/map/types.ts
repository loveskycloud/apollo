import type { HdMap, Vec3 } from '../core/types';

export interface MapLoader {
  format: string;
  load(source: string): Promise<HdMap>;
}

const registry = new Map<string, MapLoader>();

export function registerMapLoader(loader: MapLoader) {
  registry.set(loader.format, loader);
}

export async function loadHdMap(format: string, source: string): Promise<HdMap> {
  const loader = registry.get(format);
  if (!loader) {
    throw new Error(`Unsupported HD map format: ${format}. Please confirm the map format.`);
  }
  return loader.load(source);
}

export function distance2D(a: Vec3, b: Vec3): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function normalizeAngle(a: number) {
  let x = a;
  while (x > Math.PI) x -= Math.PI * 2;
  while (x < -Math.PI) x += Math.PI * 2;
  return x;
}

export interface LaneSnap {
  laneId: string;
  pointIndex: number;
  /** 投影到中心线的位置（仅作参考；选点落点用原始点击坐标） */
  position: Vec3;
  /** 点击点到中心线的横向距离 */
  distance: number;
  laneWidth: number;
  /** 该段中心线行驶方向 */
  heading: number;
}

type Cand = LaneSnap;

function collectLaneCandidates(map: HdMap, point: Vec3): Cand[] {
  const cands: Cand[] = [];
  for (const lane of map.lanes) {
    const pts = lane.centerline;
    const laneWidth = Math.max(0.6, lane.width || 1.2);
    if (pts.length === 0) continue;
    if (pts.length === 1) {
      cands.push({
        laneId: lane.id,
        pointIndex: 0,
        position: { ...pts[0] },
        distance: distance2D(point, pts[0]),
        laneWidth,
        heading: 0,
      });
      continue;
    }
    for (let i = 0; i < pts.length - 1; i += 1) {
      const a = pts[i];
      const b = pts[i + 1];
      const abx = b.x - a.x;
      const aby = b.y - a.y;
      const len2 = abx * abx + aby * aby;
      let t = len2 < 1e-12 ? 0 : ((point.x - a.x) * abx + (point.y - a.y) * aby) / len2;
      t = Math.max(0, Math.min(1, t));
      const position = { x: a.x + abx * t, y: a.y + aby * t, z: 0 };
      const d = distance2D(point, position);
      cands.push({
        laneId: lane.id,
        pointIndex: t < 0.5 ? i : i + 1,
        position,
        distance: d,
        laneWidth,
        heading: Math.atan2(aby, abx),
      });
    }
  }
  return cands;
}

/**
 * 投影到最近车道中心线。若提供 preferHeading，在近距候选中优先选方向接近的车道（双向叠道）。
 */
export function nearestLanePoint(
  map: HdMap,
  point: Vec3,
  preferHeading?: number,
): LaneSnap | null {
  const cands = collectLaneCandidates(map, point);
  if (cands.length === 0) return null;

  if (preferHeading == null) {
    return cands.reduce((a, b) => (a.distance <= b.distance ? a : b));
  }

  const minDist = Math.min(...cands.map((c) => c.distance));
  const pool = cands.filter(
    (c) => c.distance <= Math.max(minDist + 0.8, c.laneWidth * 0.6),
  );
  const ANGLE_W = 2.5;
  let best = pool[0] ?? cands[0];
  let bestScore = Infinity;
  for (const c of pool.length ? pool : cands) {
    const ang = Math.abs(normalizeAngle(c.heading - preferHeading));
    const score = c.distance + ANGLE_W * ang;
    if (score < bestScore) {
      bestScore = score;
      best = c;
    }
  }
  return best;
}

/**
 * 点是否落在某条车道半宽内（不吸附；返回最近车道信息供朝向参考）。
 */
export function pointInLane(
  map: HdMap,
  point: Vec3,
  preferHeading?: number,
): LaneSnap | null {
  const snap = nearestLanePoint(map, point, preferHeading);
  if (!snap) return null;
  const maxLat = Math.max(snap.laneWidth * 0.55, 0.45);
  if (snap.distance > maxLat) return null;
  return snap;
}

/**
 * @deprecated 选点请用 pointInLane + 原始坐标；保留供旧逻辑兼容。
 */
export function snapToLane(
  map: HdMap,
  point: Vec3,
  preferHeading?: number,
): LaneSnap | null {
  return pointInLane(map, point, preferHeading);
}
