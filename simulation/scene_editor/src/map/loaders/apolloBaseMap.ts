import type { HdMap, MapLane, MapNode, Vec3 } from '../../core/types';

interface ApolloPoint {
  x?: number;
  y?: number;
  z?: number;
}

interface ApolloCurve {
  segment?: Array<{
    line_segment?: { point?: ApolloPoint[] };
    curve_segment?: { point?: ApolloPoint[] };
  }>;
}

interface ApolloLane {
  id?: { id?: string } | string;
  name?: string;
  central_curve?: ApolloCurve;
  left_boundary?: { curve?: ApolloCurve };
  right_boundary?: { curve?: ApolloCurve };
  length?: number;
  width?: number;
  turn?: string;
  successor_id?: Array<{ id?: string } | string>;
  predecessor_id?: Array<{ id?: string } | string>;
}

interface ApolloJunction {
  id?: { id?: string } | string;
  polygon?: { point?: ApolloPoint[] };
}

interface ApolloRoad {
  id?: { id?: string } | string;
  junction_id?: { id?: string } | string;
}

export interface ApolloBaseMapJson {
  header?: {
    version?: string;
    date?: string;
    projection?: { zone_id?: number; proj?: string };
  };
  lane?: ApolloLane[];
  junction?: ApolloJunction[];
  road?: ApolloRoad[];
}

function idOf(value: { id?: string } | string | undefined, fallback: string): string {
  if (!value) return fallback;
  if (typeof value === 'string') return value;
  return value.id ?? fallback;
}

function curvePoints(curve?: ApolloCurve): Vec3[] {
  if (!curve?.segment?.length) return [];
  const points: Vec3[] = [];
  for (const seg of curve.segment) {
    const raw = seg.line_segment?.point ?? seg.curve_segment?.point ?? [];
    for (const p of raw) {
      points.push({ x: p.x ?? 0, y: p.y ?? 0, z: p.z ?? 0 });
    }
  }
  return points;
}

function estimateWidth(left: Vec3[], right: Vec3[], fallback = 3.5): number {
  if (!left.length || !right.length) return fallback;
  const a = left[Math.floor(left.length / 2)];
  const b = right[Math.floor(right.length / 2)];
  return Math.max(2, Math.hypot(a.x - b.x, a.y - b.y));
}

function boundsOf(points: Vec3[]): HdMap['bounds'] {
  if (points.length === 0) {
    return { min: { x: 0, y: 0, z: 0 }, max: { x: 100, y: 100, z: 0 } };
  }
  const min = { x: points[0].x, y: points[0].y, z: 0 };
  const max = { x: points[0].x, y: points[0].y, z: 0 };
  for (const p of points) {
    min.x = Math.min(min.x, p.x);
    min.y = Math.min(min.y, p.y);
    max.x = Math.max(max.x, p.x);
    max.y = Math.max(max.y, p.y);
  }
  return { min, max };
}

function meanOrigin(points: Vec3[]): Vec3 {
  return {
    x: points.reduce((s, p) => s + p.x, 0) / points.length,
    y: points.reduce((s, p) => s + p.y, 0) / points.length,
    z: 0,
  };
}

function offsetPoints(points: Vec3[], origin: Vec3): Vec3[] {
  return points.map((p) => ({
    x: p.x - origin.x,
    y: p.y - origin.y,
    z: p.z - origin.z,
  }));
}

/** Convert Apollo Base Map JSON (protobuf dump) into editor HdMap. */
export function parseApolloBaseMapJson(
  data: ApolloBaseMapJson,
  name = 'Apollo Base Map',
): HdMap {
  const lanes: MapLane[] = (data.lane ?? []).map((lane, index) => {
    const id = idOf(lane.id, `lane_${index}`);
    const centerline = curvePoints(lane.central_curve);
    const leftBoundary = curvePoints(lane.left_boundary?.curve);
    const rightBoundary = curvePoints(lane.right_boundary?.curve);
    const turnRaw = String(lane.turn ?? 'NO_TURN');
    const turn =
      turnRaw === 'LEFT_TURN' || turnRaw === 'RIGHT_TURN' || turnRaw === 'U_TURN' || turnRaw === 'NO_TURN'
        ? turnRaw
        : 'NO_TURN';
    return {
      id,
      name: lane.name ?? id,
      centerline,
      leftBoundary: leftBoundary.length ? leftBoundary : undefined,
      rightBoundary: rightBoundary.length ? rightBoundary : undefined,
      width: lane.width ?? estimateWidth(leftBoundary, rightBoundary),
      successors: (lane.successor_id ?? []).map((s, i) => idOf(s, `${id}_succ_${i}`)),
      predecessors: (lane.predecessor_id ?? []).map((s, i) => idOf(s, `${id}_pred_${i}`)),
      turn,
    };
  });

  const allRaw = lanes.flatMap((l) => l.centerline);
  if (allRaw.length === 0) {
    throw new Error('Apollo Base Map has no lane centerline points');
  }

  const origin = meanOrigin(allRaw);
  const normalizedLanes: MapLane[] = lanes.map((lane) => ({
    ...lane,
    centerline: offsetPoints(lane.centerline, origin),
    leftBoundary: lane.leftBoundary
      ? offsetPoints(lane.leftBoundary, origin)
      : undefined,
    rightBoundary: lane.rightBoundary
      ? offsetPoints(lane.rightBoundary, origin)
      : undefined,
  }));

  const nodes: MapNode[] = (data.junction ?? []).map((junction, index) => {
    const id = idOf(junction.id, `junction_${index}`);
    const poly = (junction.polygon?.point ?? []).map((p) => ({
      x: (p.x ?? 0) - origin.x,
      y: (p.y ?? 0) - origin.y,
      z: (p.z ?? 0) - origin.z,
    }));
    const cx = poly.reduce((s, p) => s + p.x, 0) / Math.max(poly.length, 1);
    const cy = poly.reduce((s, p) => s + p.y, 0) / Math.max(poly.length, 1);
    return {
      id,
      name: id,
      position: { x: cx, y: cy, z: 0 },
      kind: 'junction' as const,
      connectedLanes: [],
    };
  });

  // Mark lane start/end as pass nodes when no junctions exist.
  if (nodes.length === 0) {
    for (const lane of normalizedLanes) {
      if (lane.centerline.length === 0) continue;
      const start = lane.centerline[0];
      const end = lane.centerline[lane.centerline.length - 1];
      nodes.push({
        id: `${lane.id}_start`,
        name: `${lane.name} start`,
        position: start,
        kind: 'pass',
        connectedLanes: [lane.id],
      });
      nodes.push({
        id: `${lane.id}_end`,
        name: `${lane.name} end`,
        position: end,
        kind: 'pass',
        connectedLanes: [lane.id],
      });
    }
  }

  const allPoints = normalizedLanes.flatMap((l) => l.centerline);
  return {
    id: `apollo_${Date.now()}`,
    name,
    format: 'apollo_base_map',
    bounds: boundsOf(allPoints),
    lanes: normalizedLanes,
    nodes,
    meta: {
      version: data.header?.version,
      projection: 'ENU (origin-normalized)',
      roadCount: data.road?.length ?? 0,
      junctionCount: data.junction?.length ?? 0,
      origin,
    },
  };
}

export function loadApolloBaseMapFromText(text: string, name?: string): HdMap {
  const parsed = JSON.parse(text) as ApolloBaseMapJson;
  if (!parsed.lane || !Array.isArray(parsed.lane)) {
    throw new Error('Invalid Apollo Base Map JSON: missing lane[]');
  }
  return parseApolloBaseMapJson(parsed, name);
}
