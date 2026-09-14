import type { HdMap } from '../core/types';

function normalizeAngle(a: number) {
  let x = a;
  while (x > Math.PI) x -= Math.PI * 2;
  while (x < -Math.PI) x += Math.PI * 2;
  return x;
}

/**
 * 在 HD Map 中找距离 (x,y) 最近的车道中心线方向。
 * 若提供 preferHeading，则在正反向中选更接近当前朝向的一侧。
 */
export function nearestLaneHeading(
  map: HdMap,
  x: number,
  y: number,
  preferHeading?: number,
): number | null {
  return nearestLaneAt(map, x, y, preferHeading)?.heading ?? null;
}

export function nearestLaneAt(
  map: HdMap,
  x: number,
  y: number,
  preferHeading?: number,
): { heading: number; width: number; dist: number } | null {
  let bestDist = Infinity;
  let best: { heading: number; width: number; dist: number } | null = null;

  for (const lane of map.lanes) {
    const pts = lane.centerline;
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i];
      const b = pts[i + 1];
      const abx = b.x - a.x;
      const aby = b.y - a.y;
      const len2 = abx * abx + aby * aby;
      if (len2 < 1e-8) continue;
      let t = ((x - a.x) * abx + (y - a.y) * aby) / len2;
      t = Math.max(0, Math.min(1, t));
      const px = a.x + t * abx;
      const py = a.y + t * aby;
      const dist = Math.hypot(x - px, y - py);
      if (dist >= bestDist) continue;
      bestDist = dist;
      let h = Math.atan2(aby, abx);
      if (preferHeading != null) {
        const d = Math.abs(normalizeAngle(h - preferHeading));
        const dFlip = Math.abs(normalizeAngle(h + Math.PI - preferHeading));
        if (dFlip < d) h = normalizeAngle(h + Math.PI);
      }
      best = {
        heading: h,
        width: Math.max(1, lane.width || 3.5),
        dist,
      };
    }
  }

  return best;
}

/** 选中态丝带宽度：约车道 85%（参考场景编辑器宽蓝带） */
export function estimateSelectedRibbonWidth(
  map: HdMap | null,
  points: Array<{ x: number; y: number }>,
): number {
  if (!map || points.length === 0) return 1.0;
  const widths: number[] = [];
  const step = Math.max(1, Math.floor(points.length / 10));
  for (let i = 0; i < points.length; i += step) {
    const info = nearestLaneAt(map, points[i].x, points[i].y);
    if (info && info.dist < 6) widths.push(info.width);
  }
  if (!widths.length) return 1.0;
  widths.sort((a, b) => a - b);
  const med = widths[Math.floor(widths.length / 2)];
  return Math.max(0.7, Math.min(med * 0.85, 3.8));
}
