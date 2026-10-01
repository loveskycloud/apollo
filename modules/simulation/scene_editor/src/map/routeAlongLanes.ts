import type { HdMap, Vec3 } from '../core/types';
import {
  routeBetweenPoints,
  sliceCenterlineOnLane,
  type RouteBetweenOptions,
} from '../core/routing';
import { distance2D } from './types';

/** 带可选朝向的展开路点（朝向用于双向叠道选对向车道） */
export type RouteExpandPoint = Vec3 & { heading?: number };

function routeSegment(
  map: HdMap,
  a: RouteExpandPoint,
  b: RouteExpandPoint,
  opts: RouteBetweenOptions,
): Vec3[] | null {
  const segHeading = Math.atan2(b.y - a.y, b.x - a.x);
  const startHeading = a.heading ?? segHeading;
  const endHeading = b.heading ?? segHeading;
  const attempts: RouteBetweenOptions[] = [
    { ...opts, startHeading, endHeading },
    { ...opts, startHeading: undefined, endHeading: undefined },
    { ...opts, startHeading: segHeading, endHeading: segHeading },
  ];
  for (const attempt of attempts) {
    const routed = routeBetweenPoints(map, a, b, attempt);
    if (routed && routed.waypoints.length >= 2) {
      return routed.waypoints;
    }
    const sliced = sliceCenterlineOnLane(map, a, b, attempt);
    if (sliced && sliced.waypoints.length >= 2) {
      return sliced.waypoints;
    }
  }
  return null;
}

/** 将已展开路径尾部延伸到目标路点（与终点旗对齐） */
export function extendPathToTerminal(
  map: HdMap | null,
  path: Vec3[],
  terminal: Vec3,
): Vec3[] {
  if (path.length === 0) return [{ x: terminal.x, y: terminal.y, z: terminal.z ?? 0 }];
  const out = path.map((p) => ({ ...p }));
  const last = out[out.length - 1];
  const gap = distance2D(last, terminal);
  if (gap < 0.12) {
    out[out.length - 1] = { x: terminal.x, y: terminal.y, z: terminal.z ?? last.z ?? 0 };
    return out;
  }
  if (!map) {
    out.push({ x: terminal.x, y: terminal.y, z: terminal.z ?? 0 });
    return out;
  }
  const tail = routeSegment(
    map,
    { ...last, z: last.z ?? 0 },
    { ...terminal, z: terminal.z ?? 0 },
    { maxDetourRatio: 6 },
  );
  if (tail && tail.length >= 2) {
    for (let j = 1; j < tail.length; j += 1) {
      if (distance2D(out[out.length - 1], tail[j]) < 1e-3) continue;
      out.push({ x: tail[j].x, y: tail[j].y, z: terminal.z ?? 0 });
    }
    return out;
  }
  out.push({ x: terminal.x, y: terminal.y, z: terminal.z ?? 0 });
  return out;
}

/**
 * 将稀疏路点展开为沿车道中心线的折线（用于渲染）。
 * - 使用各点 heading（缺省则用段方向）做有向吸附；
 * - 寻路失败时跳过该段（不画穿地图直线）。
 */
export function expandRouteAlongLanes(
  map: HdMap | null,
  waypoints: RouteExpandPoint[],
): Vec3[] {
  if (waypoints.length === 0) return [];
  if (waypoints.length === 1 || !map) {
    return waypoints.map((p) => ({ x: p.x, y: p.y, z: p.z ?? 0 }));
  }

  const out: Vec3[] = [];
  for (let i = 0; i < waypoints.length - 1; i += 1) {
    const a = waypoints[i];
    const b = waypoints[i + 1];
    const seg = routeSegment(map, a, b, { maxDetourRatio: 6 });
    if (!seg) {
      // 寻路失败时不画直线，跳过该段
      continue;
    }

    for (let j = 0; j < seg.length; j += 1) {
      if (j === 0 && out.length > 0) {
        const last = out[out.length - 1];
        if (distance2D(last, seg[j]) < 1e-3) continue;
      }
      out.push({ x: seg[j].x, y: seg[j].y, z: a.z ?? 0 });
    }
  }
  const terminal = waypoints[waypoints.length - 1];
  return extendPathToTerminal(map, out, terminal);
}

/** Apollo/本地路径是否足够「沿车道」——拒绝稀疏直线 shortcut */
export function isPlausibleRoutePath(points: Vec3[]): boolean {
  if (points.length < 4) return false;
  let len = 0;
  for (let i = 1; i < points.length; i += 1) {
    len += distance2D(points[i - 1], points[i]);
  }
  const direct = distance2D(points[0], points[points.length - 1]);
  if (direct < 3) return points.length >= 2;
  const minPts = Math.min(24, Math.max(6, Math.ceil(direct / 2)));
  return len > direct * 1.12 && points.length >= minPts;
}
