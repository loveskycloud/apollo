/**
 * Cubic Bezier path helpers — keep algorithm in sync with
 * modules/simulation/worldsim/agent/bezier_path.cc
 */
import type { Vec3, Waypoint } from './types';

export type BezierAnchor = {
  position: Vec3;
  handleIn?: Vec3;
  handleOut?: Vec3;
};

function v2(p: Vec3): { x: number; y: number } {
  return { x: p.x, y: p.y };
}

function dist(a: { x: number; y: number }, b: { x: number; y: number }) {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

/** Evaluate cubic Bezier at t∈[0,1]. */
export function evalCubicBezier(
  p0: Vec3,
  cOut: Vec3,
  cIn: Vec3,
  p1: Vec3,
  t: number,
): Vec3 {
  const u = 1 - t;
  const uu = u * u;
  const tt = t * t;
  const x =
    uu * u * p0.x + 3 * uu * t * cOut.x + 3 * u * tt * cIn.x + tt * t * p1.x;
  const y =
    uu * u * p0.y + 3 * uu * t * cOut.y + 3 * u * tt * cIn.y + tt * t * p1.y;
  return { x, y, z: 0 };
}

/** Tangent (unnormalized) of cubic Bezier at t. */
export function tangentCubicBezier(
  p0: Vec3,
  cOut: Vec3,
  cIn: Vec3,
  p1: Vec3,
  t: number,
): { x: number; y: number } {
  const u = 1 - t;
  const x =
    3 * u * u * (cOut.x - p0.x) +
    6 * u * t * (cIn.x - cOut.x) +
    3 * t * t * (p1.x - cIn.x);
  const y =
    3 * u * u * (cOut.y - p0.y) +
    6 * u * t * (cIn.y - cOut.y) +
    3 * t * t * (p1.y - cIn.y);
  return { x, y };
}

function defaultHandlesForIndex(anchors: BezierAnchor[], i: number): {
  handleIn?: Vec3;
  handleOut?: Vec3;
} {
  const p = anchors[i].position;
  const prev = i > 0 ? anchors[i - 1].position : null;
  const next = i + 1 < anchors.length ? anchors[i + 1].position : null;
  let handleIn: Vec3 | undefined;
  let handleOut: Vec3 | undefined;
  if (prev && next) {
    const dx = next.x - prev.x;
    const dy = next.y - prev.y;
    const lenIn = dist(v2(prev), v2(p)) / 3;
    const lenOut = dist(v2(p), v2(next)) / 3;
    const L = Math.hypot(dx, dy) || 1;
    handleIn = { x: p.x - (dx / L) * lenIn, y: p.y - (dy / L) * lenIn, z: 0 };
    handleOut = { x: p.x + (dx / L) * lenOut, y: p.y + (dy / L) * lenOut, z: 0 };
  } else if (next) {
    const dx = next.x - p.x;
    const dy = next.y - p.y;
    const len = dist(v2(p), v2(next)) / 3;
    const L = Math.hypot(dx, dy) || 1;
    handleOut = { x: p.x + (dx / L) * len, y: p.y + (dy / L) * len, z: 0 };
  } else if (prev) {
    const dx = p.x - prev.x;
    const dy = p.y - prev.y;
    const len = dist(v2(prev), v2(p)) / 3;
    const L = Math.hypot(dx, dy) || 1;
    handleIn = { x: p.x - (dx / L) * len, y: p.y - (dy / L) * len, z: 0 };
  }
  return { handleIn, handleOut };
}

/** Fill missing handles; preserve existing ones. */
export function ensureBezierHandles(waypoints: Waypoint[]): Waypoint[] {
  if (waypoints.length === 0) return waypoints;
  const anchors: BezierAnchor[] = waypoints.map((w) => ({
    position: w.position,
    handleIn: w.handleIn,
    handleOut: w.handleOut,
  }));
  return waypoints.map((w, i) => {
    const auto = defaultHandlesForIndex(anchors, i);
    return {
      ...w,
      handleIn: w.handleIn ?? auto.handleIn,
      handleOut: w.handleOut ?? auto.handleOut,
    };
  });
}

/** Recompute all default handles (e.g. after insert). Keeps manually set if `preserveExisting`. */
export function refreshBezierHandles(
  waypoints: Waypoint[],
  preserveExisting = false,
): Waypoint[] {
  if (waypoints.length === 0) return waypoints;
  const anchors: BezierAnchor[] = waypoints.map((w) => ({
    position: w.position,
    handleIn: w.handleIn,
    handleOut: w.handleOut,
  }));
  return waypoints.map((w, i) => {
    const auto = defaultHandlesForIndex(anchors, i);
    return {
      ...w,
      handleIn: preserveExisting && w.handleIn ? w.handleIn : auto.handleIn,
      handleOut: preserveExisting && w.handleOut ? w.handleOut : auto.handleOut,
    };
  });
}

function segmentControls(
  a: BezierAnchor,
  b: BezierAnchor,
): { cOut: Vec3; cIn: Vec3 } {
  const cOut = a.handleOut ?? {
    x: a.position.x + (b.position.x - a.position.x) / 3,
    y: a.position.y + (b.position.y - a.position.y) / 3,
    z: 0,
  };
  const cIn = b.handleIn ?? {
    x: b.position.x - (b.position.x - a.position.x) / 3,
    y: b.position.y - (b.position.y - a.position.y) / 3,
    z: 0,
  };
  return { cOut, cIn };
}

/** Sample full path to roughly `step` meters spacing. */
export function sampleBezierPath(
  waypoints: Waypoint[],
  step = 0.25,
): Vec3[] {
  if (waypoints.length === 0) return [];
  if (waypoints.length === 1) {
    return [{ ...waypoints[0].position, z: 0 }];
  }
  const wps = ensureBezierHandles(waypoints);
  const out: Vec3[] = [];
  for (let i = 0; i < wps.length - 1; i += 1) {
    const a = wps[i];
    const b = wps[i + 1];
    const { cOut, cIn } = segmentControls(a, b);
    const chord = dist(v2(a.position), v2(b.position));
    const n = Math.max(4, Math.ceil(chord / step) * 2);
    for (let k = 0; k <= n; k += 1) {
      if (i > 0 && k === 0) continue;
      const t = k / n;
      out.push(evalCubicBezier(a.position, cOut, cIn, b.position, t));
    }
  }
  return out;
}

export type ArcSample = { x: number; y: number; s: number; heading: number };

/** Dense arc-length table for playback / sync with C++. */
export function buildArcLengthTable(
  waypoints: Waypoint[],
  step = 0.15,
): ArcSample[] {
  const pts = sampleBezierPath(waypoints, step);
  if (pts.length === 0) return [];
  const table: ArcSample[] = [];
  let s = 0;
  for (let i = 0; i < pts.length; i += 1) {
    if (i > 0) {
      s += dist(v2(pts[i - 1]), v2(pts[i]));
    }
    let heading = 0;
    if (i + 1 < pts.length) {
      heading = Math.atan2(pts[i + 1].y - pts[i].y, pts[i + 1].x - pts[i].x);
    } else if (i > 0) {
      heading = Math.atan2(pts[i].y - pts[i - 1].y, pts[i].x - pts[i - 1].x);
    }
    table.push({ x: pts[i].x, y: pts[i].y, s, heading });
  }
  return table;
}
