import type { HdMap, Vec3 } from '../core/types';
import { distance2D, nearestLanePoint } from '../map/types';

/**
 * 车道图两点路由——语义对齐 Apollo routing 模块：
 *
 * - 有向图：车道中心线方向即行驶方向，只能沿 successor 前进，不可逆行；
 * - 拓扑连接：仅按地图声明的 successor/predecessor；
 * - 起终点吸附支持 preferHeading（双向叠道选对向车道）；
 * - 绕行过长视为失败，避免整图乱绕。
 */

interface LaneEdge {
  toLaneId: string;
  toIndex: number;
  weight: number;
  path: Vec3[];
}

interface LaneGraph {
  laneEdges: Map<string, LaneEdge[]>;
  crossEdges: Map<string, LaneEdge[]>;
}

export type RouteBetweenOptions = {
  startHeading?: number;
  endHeading?: number;
  /** 相对直线距离的最大绕行倍数，默认 6；超出返回 null */
  maxDetourRatio?: number;
};

function posKey(laneId: string, index: number) {
  return `${laneId}#${index}`;
}

function densifySegment(a: Vec3, b: Vec3, maxStep = 0.45): Vec3[] {
  const dist = distance2D(a, b);
  const n = Math.max(1, Math.ceil(dist / maxStep));
  const pts: Vec3[] = [];
  for (let k = 0; k <= n; k += 1) {
    const t = k / n;
    pts.push({
      x: a.x + (b.x - a.x) * t,
      y: a.y + (b.y - a.y) * t,
      z: a.z + (b.z - a.z) * t,
    });
  }
  return pts;
}

function densifyPath(points: Vec3[], maxStep = 0.45): Vec3[] {
  if (points.length < 2) return points;
  const out: Vec3[] = [{ ...points[0] }];
  for (let i = 1; i < points.length; i += 1) {
    const seg = densifySegment(points[i - 1], points[i], maxStep);
    for (let k = 1; k < seg.length; k += 1) out.push(seg[k]);
  }
  return out;
}

function buildGraph(map: HdMap): LaneGraph {
  const laneEdges = new Map<string, LaneEdge[]>();
  const crossEdges = new Map<string, LaneEdge[]>();

  const addLaneEdge = (fromKey: string, edge: LaneEdge) => {
    const list = laneEdges.get(fromKey) ?? [];
    list.push(edge);
    laneEdges.set(fromKey, list);
  };
  const addCrossEdge = (fromKey: string, edge: LaneEdge) => {
    const list = crossEdges.get(fromKey) ?? [];
    list.push(edge);
    crossEdges.set(fromKey, list);
  };

  for (const lane of map.lanes) {
    const pts = lane.centerline;
    for (let i = 0; i + 1 < pts.length; i += 1) {
      addLaneEdge(posKey(lane.id, i), {
        toLaneId: lane.id,
        toIndex: i + 1,
        weight: distance2D(pts[i], pts[i + 1]),
        path: [pts[i], pts[i + 1]],
      });
    }

    const connectLaneEnds = (fromLaneId: string, toLaneId: string) => {
      const from = map.lanes.find((l) => l.id === fromLaneId);
      const to = map.lanes.find((l) => l.id === toLaneId);
      const a = from?.centerline.at(-1);
      const b = to?.centerline[0];
      if (!from || !to || !a || !b) return;
      const d = distance2D(a, b);
      addCrossEdge(posKey(fromLaneId, from.centerline.length - 1), {
        toLaneId,
        toIndex: 0,
        weight: d * (d > 5 ? 3 : 1),
        path: densifySegment(a, b, 0.45),
      });
    };

    for (const nextId of lane.successors) connectLaneEnds(lane.id, nextId);
    for (const prevId of lane.predecessors) connectLaneEnds(prevId, lane.id);
  }

  return { laneEdges, crossEdges };
}

export function routeBetweenPoints(
  map: HdMap,
  start: Vec3,
  end: Vec3,
  opts: RouteBetweenOptions = {},
): { waypoints: Vec3[]; length: number } | null {
  const startHeading = opts.startHeading;
  const endHeading = opts.endHeading ?? opts.startHeading;
  const maxDetourRatio = opts.maxDetourRatio ?? 6;

  const startSnap = nearestLanePoint(map, start, startHeading);
  const endSnap = nearestLanePoint(map, end, endHeading);
  if (!startSnap || !endSnap) return null;

  // 同点：无需寻路
  if (
    startSnap.laneId === endSnap.laneId &&
    startSnap.pointIndex === endSnap.pointIndex
  ) {
    const pts = densifyPath([startSnap.position, endSnap.position], 0.75);
    return { waypoints: pts, length: distance2D(startSnap.position, endSnap.position) };
  }

  const { laneEdges, crossEdges } = buildGraph(map);
  const startPos = posKey(startSnap.laneId, startSnap.pointIndex);
  const endPos = posKey(endSnap.laneId, endSnap.pointIndex);

  // 同车道但终点索引在起点之前：有向图无法倒退，视为失败（由上层用朝向重选车道）
  if (
    startSnap.laneId === endSnap.laneId &&
    endSnap.pointIndex < startSnap.pointIndex
  ) {
    return null;
  }

  const dist = new Map<string, number>();
  const prev = new Map<string, { key: string; path: Vec3[] }>();
  const visited = new Set<string>();
  const queue: Array<{ key: string; cost: number }> = [{ key: startPos, cost: 0 }];
  dist.set(startPos, 0);

  while (queue.length > 0) {
    queue.sort((a, b) => a.cost - b.cost);
    const current = queue.shift()!;
    if (visited.has(current.key)) continue;
    visited.add(current.key);
    if (current.key === endPos) break;

    const expand = (edge: LaneEdge) => {
      const toPos = posKey(edge.toLaneId, edge.toIndex);
      const nextCost = current.cost + edge.weight;
      if (nextCost < (dist.get(toPos) ?? Number.POSITIVE_INFINITY)) {
        dist.set(toPos, nextCost);
        prev.set(toPos, { key: current.key, path: edge.path });
        queue.push({ key: toPos, cost: nextCost });
      }
    };
    for (const e of laneEdges.get(current.key) ?? []) expand(e);
    for (const e of crossEdges.get(current.key) ?? []) expand(e);
  }

  if (!dist.has(endPos)) return null;

  const direct = Math.max(distance2D(start, end), 0.5);
  const graphCost = dist.get(endPos) ?? Number.POSITIVE_INFINITY;
  const sameLane = startSnap.laneId === endSnap.laneId;
  // 跨车道时直线距离无法衡量 U 型/环岛绕行，不能用欧氏倍数拒绝
  const maxGraphCost = sameLane
    ? direct * maxDetourRatio
    : Math.max(400, direct * 40);
  if (graphCost > maxGraphCost) {
    return null;
  }

  const points: Vec3[] = [];
  let cursor: string | undefined = endPos;
  const stack: Vec3[][] = [];
  while (cursor && cursor !== startPos) {
    const p = prev.get(cursor);
    if (!p) break;
    stack.push(p.path);
    cursor = p.key;
  }
  stack.reverse().forEach((segment) => {
    segment.forEach((pt, idx) => {
      if (idx === 0 && points.length > 0) return;
      points.push(pt);
    });
  });

  if (points.length === 0) {
    points.push(startSnap.position, endSnap.position);
  } else {
    if (distance2D(points[0], startSnap.position) > 1e-3) {
      points.unshift(startSnap.position);
    }
    if (distance2D(points[points.length - 1], endSnap.position) > 1e-3) {
      points.push(endSnap.position);
    }
  }

  const dense = densifyPath(points, 0.75);
  let length = 0;
  for (let i = 1; i < dense.length; i += 1) {
    length += distance2D(dense[i - 1], dense[i]);
  }
  const maxPathLen = sameLane
    ? direct * maxDetourRatio
    : Math.max(400, direct * 40);
  if (length > maxPathLen) return null;

  return { waypoints: dense, length };
}

/** 同车道内沿中心线切片（有向：end 索引须 >= start） */
export function sliceCenterlineOnLane(
  map: HdMap,
  start: Vec3,
  end: Vec3,
  opts: RouteBetweenOptions = {},
): { waypoints: Vec3[]; length: number } | null {
  const startSnap = nearestLanePoint(map, start, opts.startHeading);
  const endSnap = nearestLanePoint(map, end, opts.endHeading ?? opts.startHeading);
  if (!startSnap || !endSnap || startSnap.laneId !== endSnap.laneId) {
    return null;
  }
  if (endSnap.pointIndex < startSnap.pointIndex) {
    return null;
  }
  const lane = map.lanes.find((l) => l.id === startSnap.laneId);
  if (!lane) return null;
  const points: Vec3[] = [];
  if (distance2D(startSnap.position, lane.centerline[startSnap.pointIndex]) > 1e-3) {
    points.push(startSnap.position);
  }
  for (let i = startSnap.pointIndex; i <= endSnap.pointIndex; i += 1) {
    const pt = lane.centerline[i];
    if (points.length > 0 && distance2D(points[points.length - 1], pt) < 1e-3) {
      continue;
    }
    points.push({ x: pt.x, y: pt.y, z: pt.z ?? 0 });
  }
  if (distance2D(points[points.length - 1], endSnap.position) > 1e-3) {
    points.push(endSnap.position);
  }
  if (points.length < 2) return null;
  let length = 0;
  for (let i = 1; i < points.length; i += 1) {
    length += distance2D(points[i - 1], points[i]);
  }
  return { waypoints: points, length };
}
