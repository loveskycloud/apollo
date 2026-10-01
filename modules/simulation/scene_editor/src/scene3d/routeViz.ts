import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import type { Agent, RuntimeAgentState, Vec3 } from '../core/types';

/** 参考桌面编辑器：多 Route 配色（route 0 蓝 / route 1 绿 / …） */
export const ROUTE_PALETTE = [
  0x3b82f6, // blue
  0x22c55e, // green
  0xf59e0b, // amber
  0xa855f7, // purple
  0x06b6d4, // cyan
  0xf43f5e, // rose
] as const;

/** 参考 183214：未选中轨迹 — 柔和黄绿（禁用 tone mapping） */
export const ROUTE_COLOR_IDLE = 0x9aab66;
/** 仿真 routing 预览折线 */
export const ROUTING_PREVIEW_LINE = 0x64748b;
export const ROUTING_PREVIEW_LINE_OPACITY = 0.42;
export const ROUTING_PREVIEW_GHOST_BODY = 0xcbd5e1;
export const ROUTING_PREVIEW_GHOST_EDGE = 0x94a3b8;
/** 参考 183214：轨迹起/终点圆点 — 红 */
export const ROUTE_ENDPOINT_DOT = 0xe74c3c;
/** 参考 183104：选中 Ego 丝带蓝 */
export const ROUTE_COLOR_EGO_SELECTED = 0x3b82f6;
/** 选中 NPC 丝带绿 */
export const ROUTE_COLOR_NPC_SELECTED = 0x22c55e;

/** 行人贝塞尔轨迹：品红虚线 */
export const PEDESTRIAN_ROUTE_COLOR = 0xf472b6;
export const PEDESTRIAN_HANDLE_COLOR = 0xfbcfe8;

/** 路径显示从车辆中心 (x,y,z) 起笔，z 为车高一半（几何中心） */
export function buildRoutePathFromAgent(
  agent: Agent,
  waypoints: Vec3[],
  runtime: Record<string, RuntimeAgentState>,
): Vec3[] {
  const pos = runtime[agent.id]?.position ?? agent.position;
  const routeZ = agent.size.z / 2;
  const origin: Vec3 = { x: pos.x, y: pos.y, z: routeZ };
  if (waypoints.length === 0) return [origin];
  const rest = waypoints.map((w) => ({ x: w.x, y: w.y, z: routeZ }));
  const d0 = Math.hypot(rest[0].x - origin.x, rest[0].y - origin.y);
  if (d0 < 0.2) return [origin, ...rest.slice(1)];
  return [origin, ...rest];
}

function dedupePoints(points: THREE.Vector3[], eps = 0.08): THREE.Vector3[] {
  if (points.length === 0) return [];
  const out: THREE.Vector3[] = [points[0].clone()];
  for (let i = 1; i < points.length; i += 1) {
    if (out[out.length - 1].distanceTo(points[i]) > eps) {
      out.push(points[i].clone());
    }
  }
  return out;
}

/** 对稀疏路点做轻量平滑；已是密集车道折线时不再 Catmull，避免弯道扭曲 */
function prepareRibbonPoints(points: THREE.Vector3[]): THREE.Vector3[] {
  const cleaned = dedupePoints(points, 0.05);
  if (cleaned.length < 2) return cleaned;
  if (cleaned.length >= 24) {
    // 沿车道展开后的折线：直接斜接即可
    return cleaned;
  }
  if (cleaned.length < 3) return cleaned;
  const curve = new THREE.CatmullRomCurve3(cleaned, false, 'catmullrom', 0.35);
  let len = 0;
  for (let i = 1; i < cleaned.length; i += 1) {
    len += cleaned[i - 1].distanceTo(cleaned[i]);
  }
  const count = Math.max(12, Math.ceil(len / 0.35) + 1);
  return curve.getPoints(count);
}

/** 连续斜接丝带（bevel join，避免 miter 尖刺） */
function buildRibbonGeometry(points: THREE.Vector3[], halfWidth: number): THREE.BufferGeometry | null {
  if (points.length < 2 || halfWidth <= 0) return null;

  const left: number[] = [];
  const right: number[] = [];

  for (let i = 0; i < points.length; i += 1) {
    const curr = points[i];
    const prev = points[Math.max(0, i - 1)];
    const next = points[Math.min(points.length - 1, i + 1)];

    let tx: number;
    let ty: number;
    if (i === 0) {
      tx = next.x - curr.x;
      ty = next.y - curr.y;
    } else if (i === points.length - 1) {
      tx = curr.x - prev.x;
      ty = curr.y - prev.y;
    } else {
      tx = next.x - prev.x;
      ty = next.y - prev.y;
    }
    const tlen = Math.hypot(tx, ty) || 1;
    const nx = (-ty / tlen) * halfWidth;
    const ny = (tx / tlen) * halfWidth;

    left.push(curr.x + nx, curr.y + ny, 0.11);
    right.push(curr.x - nx, curr.y - ny, 0.11);
  }

  const positions: number[] = [];
  const indices: number[] = [];
  for (let i = 0; i < points.length; i += 1) {
    positions.push(left[i * 3], left[i * 3 + 1], left[i * 3 + 2]);
    positions.push(right[i * 3], right[i * 3 + 1], right[i * 3 + 2]);
  }
  for (let i = 0; i < points.length - 1; i += 1) {
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

/**
 * 选中车辆：参考 183104 — 宽半透明丝带（约车道 85% 宽）
 */
export function addRouteRibbon(
  group: THREE.Group,
  points: THREE.Vector3[],
  color: number,
  width: number,
  opacity = 0.52,
) {
  if (points.length < 2) return;
  const ribbonPts = prepareRibbonPoints(points);
  const half = Math.max(0.25, width / 2);
  const geo = buildRibbonGeometry(ribbonPts, half);
  if (!geo) return;

  group.add(
    new THREE.Mesh(
      geo,
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity,
        side: THREE.DoubleSide,
        depthWrite: false,
      }),
    ),
  );
}

/** 折线圆柱管（逐段对齐，避免 CatmullRom 弯道自交导致 Z-fighting） */
function buildPolylineTubeGeometry(
  points: THREE.Vector3[],
  radius: number,
): THREE.BufferGeometry | null {
  const pts = dedupePoints(points, 0.04);
  if (pts.length < 2 || radius <= 0) return null;

  const parts: THREE.BufferGeometry[] = [];
  const axis = new THREE.Vector3(0, 1, 0);
  const dir = new THREE.Vector3();
  const mid = new THREE.Vector3();
  const quat = new THREE.Quaternion();
  const mat4 = new THREE.Matrix4();
  const scale = new THREE.Vector3(1, 1, 1);

  for (let i = 0; i < pts.length - 1; i += 1) {
    const a = pts[i];
    const b = pts[i + 1];
    dir.subVectors(b, a);
    const len = dir.length();
    if (len < 0.002) continue;

    const cyl = new THREE.CylinderGeometry(radius, radius, len, 8, 1, false);
    quat.setFromUnitVectors(axis, dir.normalize());
    mid.addVectors(a, b).multiplyScalar(0.5);
    mat4.compose(mid, quat, scale);
    cyl.applyMatrix4(mat4);
    parts.push(cyl);
  }

  if (parts.length === 0) return null;
  const merged = mergeGeometries(parts);
  parts.forEach((g) => g.dispose());
  return merged;
}

function idleRouteMaterial(color: number, opacity = 1): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    color,
    toneMapped: false,
    transparent: opacity < 1,
    opacity,
    depthTest: true,
    depthWrite: opacity >= 1,
  });
}

/**
 * 未选中：参考 183214 — 细圆柱黄绿轨迹（车高一半）+ 起终点红色小球
 */
export function addRouteTrajectoryIdle(
  group: THREE.Group,
  points: THREE.Vector3[],
  routeZ: number,
) {
  if (points.length < 2) return;
  const elevated = points.map((p) => new THREE.Vector3(p.x, p.y, routeZ));
  const tubeRadius = 0.05;
  const mat = idleRouteMaterial(ROUTE_COLOR_IDLE, 0.62);

  const tubeGeo = buildPolylineTubeGeometry(elevated, tubeRadius);
  if (tubeGeo) {
    const tube = new THREE.Mesh(tubeGeo, mat);
    tube.renderOrder = 8;
    group.add(tube);
  }

  const dotRadius = tubeRadius * 2.2;
  const dotMat = idleRouteMaterial(ROUTE_ENDPOINT_DOT);
  const addEndpointDot = (pt: THREE.Vector3) => {
    const dot = new THREE.Mesh(new THREE.SphereGeometry(dotRadius, 10, 10), dotMat);
    dot.position.copy(pt);
    dot.renderOrder = 9;
    group.add(dot);
  };

  addEndpointDot(elevated[0]);
  addEndpointDot(elevated[elevated.length - 1]);
}

export type RoutingPreviewPoint = { x: number; y: number; heading?: number };

/** Routing 模式：已落点 / 悬停虚影（带朝向三角） */
export function addRoutingWaypointGhost(
  group: THREE.Group,
  point: RoutingPreviewPoint,
  size: { x: number; y: number; z: number },
  kind: 'placed' | 'hover',
) {
  const g = new THREE.Group();
  const bodyOpacity = kind === 'placed' ? 0.28 : 0.18;
  const edgeOpacity = kind === 'placed' ? 0.5 : 0.38;
  const body = new THREE.Mesh(
    new THREE.BoxGeometry(1, 1, 1),
    new THREE.MeshBasicMaterial({
      color: ROUTING_PREVIEW_GHOST_BODY,
      transparent: true,
      opacity: bodyOpacity,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
    new THREE.LineBasicMaterial({
      color: ROUTING_PREVIEW_GHOST_EDGE,
      transparent: true,
      opacity: edgeOpacity,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  const triGeo = new THREE.BufferGeometry();
  triGeo.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(
      [0.48, 0, 0.52, 0.05, -0.32, 0.52, 0.05, 0.32, 0.52],
      3,
    ),
  );
  triGeo.setIndex([0, 1, 2]);
  const tri = new THREE.Mesh(
    triGeo,
    new THREE.MeshBasicMaterial({
      color: 0xe2e8f0,
      transparent: true,
      opacity: kind === 'placed' ? 0.55 : 0.4,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false,
    }),
  );
  g.add(body, edges, tri);
  g.position.set(point.x, point.y, size.z / 2 + 0.02);
  g.rotation.z = point.heading ?? 0;
  g.scale.set(size.x, size.y, Math.max(0.35, size.z * 0.85));
  g.renderOrder = 12;
  g.userData.pncKind = 'routing';
  group.add(g);
}

/** Routing 模式：点间预览折线 */
export function addRoutingPreviewPolyline(group: THREE.Group, points: THREE.Vector3[]) {
  if (points.length < 2) return;
  const z = 0.12;
  const elevated = points.map((p) => new THREE.Vector3(p.x, p.y, z));
  const line = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(elevated),
    new THREE.LineBasicMaterial({
      color: ROUTING_PREVIEW_LINE,
      transparent: true,
      opacity: ROUTING_PREVIEW_LINE_OPACITY,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  line.renderOrder = 11;
  line.userData.pncKind = 'routing';
  group.add(line);
}

/** @deprecated use addRouteTrajectoryIdle */
export function addRouteThinLine(
  group: THREE.Group,
  points: THREE.Vector3[],
  _color: number,
  routeZ = 0.4,
) {
  addRouteTrajectoryIdle(group, points, routeZ);
}

const pinTextureCache = new Map<string, THREE.CanvasTexture>();

function pinTexture(index: number, color: number): THREE.CanvasTexture {
  const key = `${index}:${color}`;
  const cached = pinTextureCache.get(key);
  if (cached) return cached;

  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 160;
  const ctx = canvas.getContext('2d')!;
  const hex = `#${color.toString(16).padStart(6, '0')}`;

  ctx.fillStyle = hex;
  ctx.beginPath();
  ctx.moveTo(64, 148);
  ctx.bezierCurveTo(64, 148, 18, 90, 18, 52);
  ctx.arc(64, 52, 46, Math.PI * 0.85, Math.PI * 0.15, true);
  ctx.bezierCurveTo(110, 90, 64, 148, 64, 148);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,0.4)';
  ctx.lineWidth = 2.5;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(64, 52, 28, 0, Math.PI * 2);
  ctx.fillStyle = '#ffffff';
  ctx.fill();

  ctx.fillStyle = '#1e293b';
  ctx.font = 'bold 34px system-ui,sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(index), 64, 54);

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  pinTextureCache.set(key, tex);
  return tex;
}

/** 选中时显示的水滴形编号 Pin */
export function createNumberedPin(index: number, color: number): THREE.Sprite {
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: pinTexture(index, color),
      transparent: true,
      depthTest: false,
      depthWrite: false,
    }),
  );
  sprite.scale.set(3.4, 4.2, 1);
  sprite.center.set(0.5, 0);
  sprite.renderOrder = 10;
  return sprite;
}

/**
 * 行人贝塞尔路径：品红虚线 +（选中时）控制柄。
 * samplePts / anchors / handles 均为渲染坐标。
 */
export function addPedestrianBezierRoute(
  group: THREE.Group,
  opts: {
    samplePts: THREE.Vector3[];
    anchors: Array<{ id: string; x: number; y: number }>;
    handles?: Array<{
      waypointId: string;
      which: 'in' | 'out';
      x: number;
      y: number;
      ax: number;
      ay: number;
    }>;
    agentId: string;
    routeId: string;
    selected: boolean;
    z?: number;
  },
) {
  const z = opts.z ?? 0.1;
  if (opts.samplePts.length >= 2) {
    const elevated = opts.samplePts.map((p) => new THREE.Vector3(p.x, p.y, z));
    const geo = new THREE.BufferGeometry().setFromPoints(elevated);
    const line = new THREE.Line(
      geo,
      new THREE.LineDashedMaterial({
        color: PEDESTRIAN_ROUTE_COLOR,
        dashSize: 0.4,
        gapSize: 0.22,
        transparent: true,
        opacity: opts.selected ? 0.95 : 0.55,
        depthWrite: false,
        toneMapped: false,
      }),
    );
    line.computeLineDistances();
    line.renderOrder = 11;
    group.add(line);
  }

  if (!opts.selected) return;

  for (const a of opts.anchors) {
    const pin = createNumberedPin(
      opts.anchors.findIndex((x) => x.id === a.id),
      PEDESTRIAN_ROUTE_COLOR,
    );
    pin.position.set(a.x, a.y, z + 0.25);
    group.add(pin);
  }

  for (const h of opts.handles ?? []) {
    const stem = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(h.ax, h.ay, z + 0.05),
        new THREE.Vector3(h.x, h.y, z + 0.05),
      ]),
      new THREE.LineBasicMaterial({
        color: PEDESTRIAN_HANDLE_COLOR,
        transparent: true,
        opacity: 0.75,
        depthWrite: false,
        toneMapped: false,
      }),
    );
    stem.renderOrder = 12;
    group.add(stem);

    const knob = new THREE.Mesh(
      new THREE.SphereGeometry(0.22, 12, 12),
      new THREE.MeshBasicMaterial({
        color: PEDESTRIAN_HANDLE_COLOR,
        depthTest: false,
        toneMapped: false,
      }),
    );
    knob.position.set(h.x, h.y, z + 0.08);
    knob.renderOrder = 13;
    knob.userData.bezierHandle = true;
    knob.userData.agentId = opts.agentId;
    knob.userData.routeId = opts.routeId;
    knob.userData.waypointId = h.waypointId;
    knob.userData.which = h.which;
    group.add(knob);
  }
}
