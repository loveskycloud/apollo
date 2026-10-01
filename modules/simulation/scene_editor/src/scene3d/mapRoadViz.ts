import * as THREE from 'three';
import type { HdMap, MapLane, Vec3 } from '../core/types';

/** 参考 /Users/wangsheng/code/lane：沥青路面 */
const ASPHALT = 0x2e333d;
/** 左右边界：按路面边缘示意（白）——对应 map left/right_boundary 几何 */
const EDGE_WHITE = 0xebecee;
/**
 * 逻辑车道中心线（Apollo central_curve）。
 * Dreamview 官方说明为绿色细线，不是国标黄/白路面分界标线。
 * @see https://github.com/ApolloAuto/apollo/issues/15359
 */
const LOGICAL_CENTERLINE = 0x6b8f71;
/** 导向箭头：白色路面标识（示意行驶方向，画在车道面内） */
const MARKING_WHITE = 0xf5f6f8;

export type LaneMarking = 'straight' | 'left' | 'right';

/** 国标组合箭头：单组居中施划，不拆成左右偏移 */
type ArrowGlyph =
  | 'straight'
  | 'left'
  | 'right'
  | 'straight_left'
  | 'straight_right';

function normalizeAngle(a: number): number {
  let x = a;
  while (x > Math.PI) x -= Math.PI * 2;
  while (x < -Math.PI) x += Math.PI * 2;
  return x;
}

function headingOf(dx: number, dy: number): number {
  return Math.atan2(dy, dx);
}

function polylineLength(pts: Vec3[]): number {
  let len = 0;
  for (let i = 1; i < pts.length; i += 1) {
    len += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  }
  return len;
}

/** 沿折线弧长比例取位置与切向航向 */
function sampleAlong(pts: Vec3[], t: number): { x: number; y: number; heading: number } | null {
  if (pts.length < 2) return null;
  const total = polylineLength(pts);
  if (total < 1e-6) return null;
  const target = Math.max(0, Math.min(1, t)) * total;
  let acc = 0;
  for (let i = 1; i < pts.length; i += 1) {
    const a = pts[i - 1];
    const b = pts[i];
    const seg = Math.hypot(b.x - a.x, b.y - a.y);
    if (seg < 1e-9) continue;
    if (acc + seg >= target || i === pts.length - 1) {
      const u = Math.min(1, (target - acc) / seg);
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      return {
        x: a.x + dx * u,
        y: a.y + dy * u,
        heading: headingOf(dx, dy),
      };
    }
    acc += seg;
  }
  const last = pts[pts.length - 1];
  const prev = pts[pts.length - 2];
  return {
    x: last.x,
    y: last.y,
    heading: headingOf(last.x - prev.x, last.y - prev.y),
  };
}

function classifyDelta(delta: number): LaneMarking {
  const abs = Math.abs(delta);
  if (abs < (22 * Math.PI) / 180) return 'straight';
  return delta > 0 ? 'left' : 'right';
}

/**
 * 推断车道地面标识：优先 Apollo turn；否则看末段航向变化 / 后继入口航向。
 * 本矿图多为 NO_TURN，靠几何推断直行/左/右。
 */
export function inferLaneMarkings(lane: MapLane, byId: Map<string, MapLane>): LaneMarking[] {
  if (lane.turn === 'LEFT_TURN') return ['left'];
  if (lane.turn === 'RIGHT_TURN') return ['right'];
  if (lane.turn === 'U_TURN') return ['left'];

  const marks = new Set<LaneMarking>();

  if (lane.successors.length > 0) {
    const tip = sampleAlong(lane.centerline, 0.92);
    if (tip) {
      for (const sid of lane.successors) {
        const succ = byId.get(sid);
        if (!succ || succ.centerline.length < 2) continue;
        const entry = sampleAlong(succ.centerline, 0.08);
        if (!entry) continue;
        marks.add(classifyDelta(normalizeAngle(entry.heading - tip.heading)));
      }
    }
  }

  if (marks.size === 0) {
    const mid = sampleAlong(lane.centerline, 0.55);
    const end = sampleAlong(lane.centerline, 0.95);
    if (mid && end) {
      marks.add(classifyDelta(normalizeAngle(end.heading - mid.heading)));
    } else {
      marks.add('straight');
    }
  }

  // 稳定顺序：直行、左转、右转
  const order: LaneMarking[] = ['straight', 'left', 'right'];
  return order.filter((m) => marks.has(m));
}

function buildRibbonFromCenter(
  pts: Vec3[],
  halfWidth: number,
  z: number,
): THREE.BufferGeometry | null {
  if (pts.length < 2 || halfWidth <= 0) return null;
  const left: number[] = [];
  const right: number[] = [];
  for (let i = 0; i < pts.length; i += 1) {
    const curr = pts[i];
    const prev = pts[Math.max(0, i - 1)];
    const next = pts[Math.min(pts.length - 1, i + 1)];
    let tx: number;
    let ty: number;
    if (i === 0) {
      tx = next.x - curr.x;
      ty = next.y - curr.y;
    } else if (i === pts.length - 1) {
      tx = curr.x - prev.x;
      ty = curr.y - prev.y;
    } else {
      tx = next.x - prev.x;
      ty = next.y - prev.y;
    }
    const len = Math.hypot(tx, ty) || 1;
    const nx = (-ty / len) * halfWidth;
    const ny = (tx / len) * halfWidth;
    left.push(curr.x + nx, curr.y + ny, z);
    right.push(curr.x - nx, curr.y - ny, z);
  }
  const positions: number[] = [];
  const indices: number[] = [];
  for (let i = 0; i < pts.length; i += 1) {
    positions.push(left[i * 3], left[i * 3 + 1], left[i * 3 + 2]);
    positions.push(right[i * 3], right[i * 3 + 1], right[i * 3 + 2]);
  }
  for (let i = 0; i < pts.length - 1; i += 1) {
    const a = i * 2;
    const b = a + 1;
    const c = a + 2;
    const d = a + 3;
    indices.push(a, b, c, b, d, c);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setIndex(indices);
  return geo;
}

function asphaltFromBoundaries(lane: MapLane, z: number): THREE.Mesh | null {
  if (!lane.leftBoundary || !lane.rightBoundary || lane.leftBoundary.length < 2) return null;
  const shapePoints = [
    ...lane.leftBoundary.map((p) => new THREE.Vector2(p.x, p.y)),
    ...[...lane.rightBoundary].reverse().map((p) => new THREE.Vector2(p.x, p.y)),
  ];
  const mesh = new THREE.Mesh(
    new THREE.ShapeGeometry(new THREE.Shape(shapePoints)),
    new THREE.MeshBasicMaterial({
      color: ASPHALT,
      side: THREE.DoubleSide,
      depthWrite: true,
    }),
  );
  mesh.position.z = z;
  mesh.userData.mapKind = 'asphalt';
  return mesh;
}

function asphaltFromCenter(lane: MapLane, z: number): THREE.Mesh | null {
  const half = Math.max(0.6, (lane.width || 2.5) / 2);
  const geo = buildRibbonFromCenter(lane.centerline, half, z);
  if (!geo) return null;
  const mesh = new THREE.Mesh(
    geo,
    new THREE.MeshBasicMaterial({
      color: ASPHALT,
      side: THREE.DoubleSide,
      depthWrite: true,
    }),
  );
  mesh.userData.mapKind = 'asphalt';
  return mesh;
}

function edgeLineMesh(boundary: Vec3[], z: number, halfPaint = 0.07): THREE.Mesh | null {
  const geo = buildRibbonFromCenter(boundary, halfPaint, z);
  if (!geo) return null;
  const mesh = new THREE.Mesh(
    geo,
    new THREE.MeshBasicMaterial({
      color: EDGE_WHITE,
      side: THREE.DoubleSide,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  mesh.userData.mapKind = 'edge';
  return mesh;
}

/**
 * 逻辑中心线：连续细绿线（HD map overlay），勿画成黄虚「对向分界线」。
 */
function logicalCenterlineMesh(centerline: Vec3[], z: number): THREE.Mesh | null {
  if (centerline.length < 2) return null;
  const geo = buildRibbonFromCenter(centerline, 0.04, z);
  if (!geo) return null;
  const mesh = new THREE.Mesh(
    geo,
    new THREE.MeshBasicMaterial({
      color: LOGICAL_CENTERLINE,
      transparent: true,
      opacity: 0.85,
      side: THREE.DoubleSide,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  mesh.userData.mapKind = 'logicalCenterline';
  return mesh;
}

/** 缩小标识：约车道宽 35%，全长约 1.2～1.6m */
function arrowSize(laneWidth: number): { L: number; W: number } {
  const L = Math.min(1.6, Math.max(1.15, laneWidth * 0.55));
  const W = Math.min(laneWidth * 0.35, L * 0.42);
  return { L, W };
}

function resolveGlyph(marks: LaneMarking[]): ArrowGlyph {
  const s = marks.includes('straight');
  const l = marks.includes('left');
  const r = marks.includes('right');
  if (s && l && !r) return 'straight_left';
  if (s && r && !l) return 'straight_right';
  if (l && !r && !s) return 'left';
  if (r && !l && !s) return 'right';
  if (l && r) return 'left'; // 左+右少见，优先左转
  return 'straight';
}

/** 直行（箭尾原点，+Y 前进） */
function shapeStraight(L: number, W: number): THREE.Shape {
  const sw = W * 0.18;
  const hw = W * 0.5;
  const hl = L * 0.34;
  const shaft = L - hl;
  const s = new THREE.Shape();
  s.moveTo(-sw, 0);
  s.lineTo(sw, 0);
  s.lineTo(sw, shaft);
  s.lineTo(hw, shaft);
  s.lineTo(0, L);
  s.lineTo(-hw, shaft);
  s.lineTo(-sw, shaft);
  s.closePath();
  return s;
}

/**
 * 左转：竖杆 + 向左三角箭头头（路口进口道常见样式，见图示左转弯专用道）。
 */
function shapeLeft(L: number, W: number): THREE.Shape {
  const sw = W * 0.16;
  const stem = L * 0.58;
  const neckX = -W * 0.08;
  const tipX = -W * 0.95;
  const tipY = stem + L * 0.02;
  const flair = W * 0.48;
  const s = new THREE.Shape();
  s.moveTo(-sw, 0);
  s.lineTo(sw, 0);
  s.lineTo(sw, stem - sw * 0.8);
  s.lineTo(neckX, stem - sw * 0.8);
  s.lineTo(neckX, tipY + flair * 0.55);
  s.lineTo(tipX, tipY);
  s.lineTo(neckX, tipY - flair * 0.55);
  s.lineTo(neckX, stem - sw * 2.4);
  s.lineTo(-sw, stem - sw * 2.4);
  s.closePath();
  return s;
}

function shapeRight(L: number, W: number): THREE.Shape {
  const pts = shapeLeft(L, W).getPoints(32);
  const s = new THREE.Shape();
  pts.forEach((p, i) => {
    if (i === 0) s.moveTo(-p.x, p.y);
    else s.lineTo(-p.x, p.y);
  });
  s.closePath();
  return s;
}

/**
 * 直行或右转组合箭头：主杆直行箭头头 + 右侧分支箭头（见图示最右车道）。
 */
function shapeStraightRight(L: number, W: number): THREE.Shape {
  const sw = W * 0.15;
  const hw = W * 0.42;
  const hl = L * 0.28;
  const shaft = L - hl;
  const branchY = L * 0.42;
  const branchTipX = W * 0.95;
  const branchTipY = branchY + L * 0.08;
  const flair = W * 0.4;

  const s = new THREE.Shape();
  // 外轮廓：尾 → 右下枝头 → 回杆 → 直行头 → 左杆 → 尾
  s.moveTo(-sw, 0);
  s.lineTo(sw, 0);
  s.lineTo(sw, branchY - sw);
  // 右分支下缘
  s.lineTo(W * 0.22, branchY - sw);
  s.lineTo(W * 0.22, branchTipY - flair * 0.5);
  s.lineTo(branchTipX, branchTipY);
  s.lineTo(W * 0.22, branchTipY + flair * 0.5);
  s.lineTo(W * 0.22, branchY + sw);
  s.lineTo(sw, branchY + sw);
  s.lineTo(sw, shaft);
  s.lineTo(hw, shaft);
  s.lineTo(0, L);
  s.lineTo(-hw, shaft);
  s.lineTo(-sw, shaft);
  s.closePath();
  return s;
}

function shapeStraightLeft(L: number, W: number): THREE.Shape {
  const pts = shapeStraightRight(L, W).getPoints(40);
  const s = new THREE.Shape();
  pts.forEach((p, i) => {
    if (i === 0) s.moveTo(-p.x, p.y);
    else s.lineTo(-p.x, p.y);
  });
  s.closePath();
  return s;
}

function glyphShape(kind: ArrowGlyph, L: number, W: number): THREE.Shape {
  switch (kind) {
    case 'left':
      return shapeLeft(L, W);
    case 'right':
      return shapeRight(L, W);
    case 'straight_left':
      return shapeStraightLeft(L, W);
    case 'straight_right':
      return shapeStraightRight(L, W);
    default:
      return shapeStraight(L, W);
  }
}

/** 每条车道只施划一组，放在中段 */
function markingFractions(_totalLen: number): number[] {
  return [0.5];
}

/**
 * 导向箭头居中画在车道面内（中心线中段），每车道一组。
 */
/** 预留：白色导向箭头（addMapRoads 内按需恢复调用） */
export function addTurnMarkings(
  group: THREE.Group,
  lane: MapLane,
  markings: LaneMarking[],
  z: number,
) {
  if (markings.length === 0 || lane.centerline.length < 2) return;
  const { L, W } = arrowSize(lane.width || 2.5);
  const glyph = resolveGlyph(markings);
  const fractions = markingFractions(polylineLength(lane.centerline));

  for (const t of fractions) {
    const sample = sampleAlong(lane.centerline, t);
    if (!sample) continue;
    const shape = glyphShape(glyph, L, W);
    const geo = new THREE.ShapeGeometry(shape);
    geo.rotateZ(sample.heading - Math.PI / 2);
    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshBasicMaterial({
        color: MARKING_WHITE,
        side: THREE.DoubleSide,
        depthWrite: false,
        toneMapped: false,
      }),
    );
    const back = L * 0.48;
    mesh.position.set(
      sample.x - Math.cos(sample.heading) * back,
      sample.y - Math.sin(sample.heading) * back,
      z,
    );
    mesh.userData.mapKind = 'marking';
    mesh.userData.marking = glyph;
    group.add(mesh);
  }
}

/**
 * 沥青 + 白边（边界几何）+ 绿色逻辑中心线 + 每车道一组导向箭头。
 */
export function addMapRoads(
  group: THREE.Group,
  map: HdMap,
  layers: { lanes: boolean; boundaries: boolean },
) {
  if (layers.lanes) {
    for (const lane of map.lanes) {
      if (lane.centerline.length < 2) continue;
      const fill =
        asphaltFromBoundaries(lane, 0.01) ?? asphaltFromCenter(lane, 0.01);
      if (fill) group.add(fill);

      const center = logicalCenterlineMesh(lane.centerline, 0.035);
      if (center) group.add(center);

      // 白色导向箭头暂不绘制（可按需恢复 addTurnMarkings）
    }
  }

  if (layers.boundaries) {
    for (const lane of map.lanes) {
      for (const boundary of [lane.leftBoundary, lane.rightBoundary]) {
        if (!boundary || boundary.length < 2) continue;
        const edge = edgeLineMesh(boundary, 0.04);
        if (edge) group.add(edge);
      }
      if (
        (!lane.leftBoundary || lane.leftBoundary.length < 2) &&
        (!lane.rightBoundary || lane.rightBoundary.length < 2)
      ) {
        const half = Math.max(0.6, (lane.width || 2.5) / 2);
        const left: Vec3[] = [];
        const right: Vec3[] = [];
        for (let i = 0; i < lane.centerline.length; i += 1) {
          const curr = lane.centerline[i];
          const prev = lane.centerline[Math.max(0, i - 1)];
          const next = lane.centerline[Math.min(lane.centerline.length - 1, i + 1)];
          let tx =
            i === 0
              ? next.x - curr.x
              : i === lane.centerline.length - 1
                ? curr.x - prev.x
                : next.x - prev.x;
          let ty =
            i === 0
              ? next.y - curr.y
              : i === lane.centerline.length - 1
                ? curr.y - prev.y
                : next.y - prev.y;
          const len = Math.hypot(tx, ty) || 1;
          const nx = (-ty / len) * half;
          const ny = (tx / len) * half;
          left.push({ x: curr.x + nx, y: curr.y + ny, z: 0 });
          right.push({ x: curr.x - nx, y: curr.y - ny, z: 0 });
        }
        const el = edgeLineMesh(left, 0.04);
        const er = edgeLineMesh(right, 0.04);
        if (el) group.add(el);
        if (er) group.add(er);
      }
    }
  }
}
