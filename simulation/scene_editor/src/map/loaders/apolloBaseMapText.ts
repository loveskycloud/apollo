import type { HdMap, MapLane, MapNode, Vec3 } from '../../core/types';

type ProtoValue = string | number | boolean | ProtoObject | ProtoValue[];
type ProtoObject = { [key: string]: ProtoValue };

function tokenize(text: string): string[] {
  const tokens: string[] = [];
  const re = /"[^"\\]*(?:\\.[^"\\]*)*"|[A-Za-z_][\w.]*|:|{|}|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    tokens.push(m[0]);
  }
  return tokens;
}

function parseValue(tokens: string[], i: { at: number }): ProtoValue {
  const tok = tokens[i.at];
  if (tok === '{') {
    i.at += 1;
    return parseObject(tokens, i);
  }
  if (tok === 'true' || tok === 'false') {
    i.at += 1;
    return tok === 'true';
  }
  if (tok.startsWith('"')) {
    i.at += 1;
    return tok.slice(1, -1);
  }
  if (!Number.isNaN(Number(tok))) {
    i.at += 1;
    return Number(tok);
  }
  // enum-like identifier
  i.at += 1;
  return tok;
}

function parseObject(tokens: string[], i: { at: number }): ProtoObject {
  const obj: ProtoObject = {};
  while (i.at < tokens.length && tokens[i.at] !== '}') {
    const key = tokens[i.at++];
    if (tokens[i.at] !== ':') {
      // message field: key { ... }
      if (tokens[i.at] === '{') {
        i.at += 1;
        const value = parseObject(tokens, i);
        appendField(obj, key, value);
        continue;
      }
      throw new Error(`Expected ':' or '{' after ${key}`);
    }
    i.at += 1; // :
    const value = parseValue(tokens, i);
    appendField(obj, key, value);
  }
  if (tokens[i.at] === '}') i.at += 1;
  return obj;
}

function appendField(obj: ProtoObject, key: string, value: ProtoValue) {
  if (key in obj) {
    const existing = obj[key];
    if (Array.isArray(existing)) existing.push(value);
    else obj[key] = [existing, value];
  } else {
    obj[key] = value;
  }
}

function asArray<T>(value: T | T[] | undefined): T[] {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

function idOf(value: unknown, fallback: string): string {
  if (value == null) return fallback;
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (typeof value === 'object' && value && 'id' in value) {
    const inner = (value as { id?: unknown }).id;
    if (typeof inner === 'string' || typeof inner === 'number') return String(inner);
  }
  return fallback;
}

function pointsFromCurve(curve: unknown): Vec3[] {
  if (!curve || typeof curve !== 'object') return [];
  const segments = asArray((curve as ProtoObject).segment as ProtoObject | ProtoObject[]);
  const points: Vec3[] = [];
  for (const seg of segments) {
    const line = (seg.line_segment ?? seg.curve_segment) as ProtoObject | undefined;
    const raw = asArray(line?.point as ProtoObject | ProtoObject[]);
    for (const p of raw) {
      points.push({
        x: Number(p.x ?? 0),
        y: Number(p.y ?? 0),
        z: Number(p.z ?? 0),
      });
    }
  }
  return points;
}

function widthFromSamples(lane: ProtoObject, fallback = 3.5): number {
  const left = asArray(lane.left_sample as ProtoObject | ProtoObject[]);
  const right = asArray(lane.right_sample as ProtoObject | ProtoObject[]);
  if (!left.length && !right.length) return fallback;
  const lw = left.map((s) => Number(s.width ?? 0)).filter((w) => w > 0);
  const rw = right.map((s) => Number(s.width ?? 0)).filter((w) => w > 0);
  // Apollo sample.width = 中心线到边界的距离（半宽）
  const avgL = lw.length ? lw.reduce((a, b) => a + b, 0) / lw.length : 0;
  const avgR = rw.length ? rw.reduce((a, b) => a + b, 0) / rw.length : 0;
  const full = avgL + avgR;
  return full > 0.2 ? full : fallback;
}

/** 由左右边界几何推算车道全宽（与视口填充绘制一致） */
function widthFromBoundaries(
  centerline: Vec3[],
  leftBoundary?: Vec3[],
  rightBoundary?: Vec3[],
): number | null {
  if (!centerline.length || !leftBoundary?.length || !rightBoundary?.length) return null;
  const samples: number[] = [];
  const step = Math.max(1, Math.floor(centerline.length / 12));
  for (let i = 0; i < centerline.length; i += step) {
    const c = centerline[i];
    const prev = centerline[Math.max(0, i - 1)];
    const next = centerline[Math.min(centerline.length - 1, i + 1)];
    const tx = next.x - prev.x;
    const ty = next.y - prev.y;
    const tlen = Math.hypot(tx, ty) || 1;
    const nx = -ty / tlen;
    const ny = tx / tlen;
    // 按中心线进度对齐左右边界点，再投影到法向（避免最近点跨到远端边界）
    const t = centerline.length <= 1 ? 0 : i / (centerline.length - 1);
    const li = Math.round(t * (leftBoundary.length - 1));
    const ri = Math.round(t * (rightBoundary.length - 1));
    const L = leftBoundary[li];
    const R = rightBoundary[ri];
    const leftW = Math.abs((L.x - c.x) * nx + (L.y - c.y) * ny);
    const rightW = Math.abs((R.x - c.x) * nx + (R.y - c.y) * ny);
    const w = leftW + rightW;
    if (w > 0.3 && w < 8) samples.push(w);
  }
  if (!samples.length) return null;
  samples.sort((a, b) => a - b);
  return samples[Math.floor(samples.length / 2)];
}

function offsetPoints(points: Vec3[], origin: Vec3): Vec3[] {
  return points.map((p) => ({
    x: p.x - origin.x,
    y: p.y - origin.y,
    z: p.z - origin.z,
  }));
}

/** 加密折线，避免转弯处仅靠稀疏点拉直线切角 */
function densifyPolyline(points: Vec3[], maxStep = 0.45): Vec3[] {
  if (points.length < 2) return points;
  const out: Vec3[] = [{ ...points[0] }];
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    const dist = Math.hypot(b.x - a.x, b.y - a.y);
    const n = Math.max(1, Math.ceil(dist / maxStep));
    for (let k = 1; k <= n; k += 1) {
      const t = k / n;
      out.push({
        x: a.x + (b.x - a.x) * t,
        y: a.y + (b.y - a.y) * t,
        z: a.z + (b.z - a.z) * t,
      });
    }
  }
  return out;
}

/** Parse Apollo HDMap protobuf text (base_map.txt). */
export function parseApolloBaseMapText(text: string, name = 'Apollo Base Map'): HdMap {
  const tokens = tokenize(text);
  const root = parseObject(tokens, { at: 0 });

  const laneObjs = asArray(root.lane as ProtoObject | ProtoObject[]);
  const junctionObjs = asArray(root.junction as ProtoObject | ProtoObject[]);
  const header = (root.header as ProtoObject) ?? {};

  const rawLanes: Array<{
    id: string;
    name: string;
    centerline: Vec3[];
    leftBoundary?: Vec3[];
    rightBoundary?: Vec3[];
    width: number;
    successors: string[];
    predecessors: string[];
    turn?: 'NO_TURN' | 'LEFT_TURN' | 'RIGHT_TURN' | 'U_TURN';
  }> = [];

  for (let i = 0; i < laneObjs.length; i += 1) {
    const lane = laneObjs[i];
    const id = idOf(lane.id, `lane_${i}`);
    const centerline = pointsFromCurve(lane.central_curve);
    const leftBoundary = pointsFromCurve((lane.left_boundary as ProtoObject)?.curve);
    const rightBoundary = pointsFromCurve((lane.right_boundary as ProtoObject)?.curve);
    const sampleWidth = widthFromSamples(lane);
    const geoWidth = widthFromBoundaries(
      centerline,
      leftBoundary.length ? leftBoundary : undefined,
      rightBoundary.length ? rightBoundary : undefined,
    );
    // 优先与绘制一致的几何宽度；异常时回退 sample
    const width =
      geoWidth && geoWidth < 6 ? geoWidth : sampleWidth > 0.2 ? sampleWidth : geoWidth ?? 2.5;
    const turnRaw = String(lane.turn ?? 'NO_TURN');
    const turn =
      turnRaw === 'LEFT_TURN' || turnRaw === 'RIGHT_TURN' || turnRaw === 'U_TURN' || turnRaw === 'NO_TURN'
        ? turnRaw
        : 'NO_TURN';
    rawLanes.push({
      id,
      name: String(lane.name ?? id),
      centerline,
      leftBoundary: leftBoundary.length ? leftBoundary : undefined,
      rightBoundary: rightBoundary.length ? rightBoundary : undefined,
      width,
      successors: asArray(lane.successor_id).map((s, idx) => idOf(s, `${id}_s${idx}`)),
      predecessors: asArray(lane.predecessor_id).map((s, idx) => idOf(s, `${id}_p${idx}`)),
      turn,
    });
  }

  const allRaw = rawLanes.flatMap((l) => l.centerline);
  if (allRaw.length === 0) {
    throw new Error('Apollo Base Map has no lane centerline points');
  }

  const origin = {
    x: allRaw.reduce((s, p) => s + p.x, 0) / allRaw.length,
    y: allRaw.reduce((s, p) => s + p.y, 0) / allRaw.length,
    z: 0,
  };

  const lanes: MapLane[] = rawLanes.map((lane) => ({
    ...lane,
    centerline: densifyPolyline(offsetPoints(lane.centerline, origin), 0.45),
    leftBoundary: lane.leftBoundary
      ? densifyPolyline(offsetPoints(lane.leftBoundary, origin), 0.6)
      : undefined,
    rightBoundary: lane.rightBoundary
      ? densifyPolyline(offsetPoints(lane.rightBoundary, origin), 0.6)
      : undefined,
  }));

  const nodes: MapNode[] = junctionObjs.map((junction, index) => {
    const id = idOf(junction.id, `junction_${index}`);
    const poly = asArray((junction.polygon as ProtoObject)?.point as ProtoObject | ProtoObject[]).map(
      (p) => ({
        x: Number(p.x ?? 0) - origin.x,
        y: Number(p.y ?? 0) - origin.y,
        z: Number(p.z ?? 0),
      }),
    );
    const cx = poly.reduce((s, p) => s + p.x, 0) / Math.max(poly.length, 1);
    const cy = poly.reduce((s, p) => s + p.y, 0) / Math.max(poly.length, 1);
    return {
      id,
      name: id,
      position: { x: cx || 0, y: cy || 0, z: 0 },
      kind: 'junction' as const,
      connectedLanes: [],
    };
  });

  if (nodes.length === 0) {
    for (const lane of lanes) {
      if (lane.centerline.length < 2) continue;
      if (lane.predecessors.length === 0) {
        nodes.push({
          id: `${lane.id}_start`,
          name: `${lane.name} start`,
          position: lane.centerline[0],
          kind: 'load',
          connectedLanes: [lane.id],
        });
      }
      if (lane.successors.length === 0) {
        nodes.push({
          id: `${lane.id}_end`,
          name: `${lane.name} end`,
          position: lane.centerline[lane.centerline.length - 1],
          kind: 'unload',
          connectedLanes: [lane.id],
        });
      }
    }
  }

  const all = lanes.flatMap((l) => l.centerline);
  const min = { x: all[0].x, y: all[0].y, z: 0 };
  const max = { x: all[0].x, y: all[0].y, z: 0 };
  for (const p of all) {
    min.x = Math.min(min.x, p.x);
    min.y = Math.min(min.y, p.y);
    max.x = Math.max(max.x, p.x);
    max.y = Math.max(max.y, p.y);
  }

  return {
    id: String(header.version ?? `apollo_${Date.now()}`),
    name,
    format: 'apollo_base_map',
    bounds: { min, max },
    lanes,
    nodes,
    meta: {
      version: String(header.version ?? ''),
      projection: 'ENU (origin-normalized)',
      roadCount: asArray(root.road).length,
      junctionCount: junctionObjs.length,
      origin,
    },
  };
}

export async function loadApolloBaseMapTxt(url: string, name?: string): Promise<HdMap> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load map: ${url}`);
  const text = await res.text();
  return parseApolloBaseMapText(text, name ?? url);
}
