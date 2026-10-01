import * as THREE from 'three';

/**
 * Planning 规划线：对齐 /Users/wangsheng/code/lane
 * - 与主车同宽的等宽 ribbon（miter + bevel）
 * - 刹车着色：巡航青绿 → 轻刹琥珀 → 急刹红（顶点色，兼容 WebGPU）
 */

export type PlanningTrajSample = {
  x: number;
  y: number;
  v?: number;
  a?: number;
  relativeTime?: number;
};

const HARD_BRAKE = 4.5; // m/s²，与 lane demo u_hardBrake 一致

type Vec2 = { x: number; y: number };

function sub(a: Vec2, b: Vec2): Vec2 {
  return { x: a.x - b.x, y: a.y - b.y };
}
function add(a: Vec2, b: Vec2): Vec2 {
  return { x: a.x + b.x, y: a.y + b.y };
}
function scale(a: Vec2, s: number): Vec2 {
  return { x: a.x * s, y: a.y * s };
}
function length(a: Vec2): number {
  return Math.hypot(a.x, a.y);
}
function normalize(a: Vec2): Vec2 {
  const L = length(a) || 1;
  return { x: a.x / L, y: a.y / L };
}
function perp(a: Vec2): Vec2 {
  return { x: -a.y, y: a.x };
}
function dot(a: Vec2, b: Vec2): number {
  return a.x * b.x + a.y * b.y;
}

function estimateAccel(samples: PlanningTrajSample[]): number[] {
  const n = samples.length;
  const out = new Array(n).fill(0);
  for (let i = 0; i < n; i += 1) {
    if (typeof samples[i].a === 'number' && Number.isFinite(samples[i].a)) {
      out[i] = samples[i].a as number;
      continue;
    }
    const i0 = Math.max(0, i - 1);
    const i1 = Math.min(n - 1, i + 1);
    const v0 = samples[i0].v ?? 0;
    const v1 = samples[i1].v ?? 0;
    const t0 = samples[i0].relativeTime ?? i0 * 0.1;
    const t1 = samples[i1].relativeTime ?? i1 * 0.1;
    const dt = t1 - t0;
    out[i] = dt > 1e-4 ? (v1 - v0) / dt : 0;
  }
  return out;
}

/** 抽稀：过密点对 miter 无益且拖慢渲染 */
function downsample(samples: PlanningTrajSample[], maxPts = 320): PlanningTrajSample[] {
  if (samples.length <= maxPts) return samples;
  const step = Math.ceil(samples.length / maxPts);
  const out: PlanningTrajSample[] = [];
  for (let i = 0; i < samples.length; i += step) out.push(samples[i]);
  const last = samples[samples.length - 1];
  if (out[out.length - 1] !== last) out.push(last);
  return out;
}

type Join = { left: Vec2; right: Vec2; bevel?: { left: Vec2; right: Vec2 } };

/**
 * Miter/Bevel 等宽挤出（移植自 lane/src/math/ribbon.ts）
 */
function buildMiterRibbon(
  centerline: Vec2[],
  halfW: number,
  accel: number[],
  z: number,
  miterLimit = 2.5,
): THREE.BufferGeometry | null {
  const n = centerline.length;
  if (n < 2 || halfW <= 0) return null;

  const dirs: Vec2[] = [];
  const normals: Vec2[] = [];
  for (let i = 0; i < n - 1; i += 1) {
    const d = normalize(sub(centerline[i + 1], centerline[i]));
    dirs.push(d);
    normals.push(perp(d));
  }

  const joins: Join[] = new Array(n);
  {
    const nl = normals[0];
    joins[0] = {
      left: add(centerline[0], scale(nl, halfW)),
      right: add(centerline[0], scale(nl, -halfW)),
    };
  }
  {
    const nl = normals[n - 2];
    joins[n - 1] = {
      left: add(centerline[n - 1], scale(nl, halfW)),
      right: add(centerline[n - 1], scale(nl, -halfW)),
    };
  }

  for (let i = 1; i < n - 1; i += 1) {
    const n0 = normals[i - 1];
    const n1 = normals[i];
    const d0 = dirs[i - 1];
    const d1 = dirs[i];
    const cross = d0.x * d1.y - d0.y * d1.x;
    const cos = Math.max(-1, Math.min(1, dot(d0, d1)));

    let miter = add(n0, n1);
    const mLen = length(miter);
    if (mLen < 1e-8) {
      joins[i] = {
        left: add(centerline[i], scale(n0, halfW)),
        right: add(centerline[i], scale(n0, -halfW)),
        bevel: {
          left: add(centerline[i], scale(n1, halfW)),
          right: add(centerline[i], scale(n1, -halfW)),
        },
      };
      continue;
    }
    miter = scale(miter, 1 / mLen);
    const denom = Math.max(dot(miter, n0), 1e-4);
    let miterLen = halfW / denom;
    const useBevel = miterLen / halfW > miterLimit || cos < -0.7;

    if (useBevel) {
      const innerSign = cross >= 0 ? 1 : -1;
      const inner = add(
        centerline[i],
        scale(miter, -innerSign * Math.min(miterLen, halfW * miterLimit)),
      );
      const outerA = add(centerline[i], scale(n0, innerSign * halfW));
      const outerB = add(centerline[i], scale(n1, innerSign * halfW));
      if (innerSign > 0) {
        joins[i] = { left: outerA, right: inner, bevel: { left: outerB, right: inner } };
      } else {
        joins[i] = { left: inner, right: outerA, bevel: { left: inner, right: outerB } };
      }
    } else {
      joins[i] = {
        left: add(centerline[i], scale(miter, miterLen)),
        right: add(centerline[i], scale(miter, -miterLen)),
      };
    }
  }

  const positions: number[] = [];
  const colors: number[] = [];
  const indices: number[] = [];
  let vCount = 0;

  const pushVert = (p: Vec2, side: number, idx: number) => {
    positions.push(p.x, p.y, z);
    const c = brakeColor(accel[idx] ?? 0);
    // 边缘略暗，保留一点条带立体感（原 shader shade）
    const shade = 0.75 + 0.25 * (1 - Math.min(1, Math.abs(side) * 0.35));
    colors.push(c.r * shade, c.g * shade, c.b * shade);
    return vCount++;
  };

  let prevL = pushVert(joins[0].left, -1, 0);
  let prevR = pushVert(joins[0].right, 1, 0);

  for (let i = 1; i < n; i += 1) {
    const j = joins[i];
    if (j.bevel) {
      const l0 = pushVert(j.left, -1, i);
      const r0 = pushVert(j.right, 1, i);
      indices.push(prevL, prevR, l0, prevR, r0, l0);
      const l1 = pushVert(j.bevel.left, -1, i);
      const r1 = pushVert(j.bevel.right, 1, i);
      indices.push(l0, r0, l1, r0, r1, l1);
      prevL = l1;
      prevR = r1;
    } else {
      const l = pushVert(j.left, -1, i);
      const r = pushVert(j.right, 1, i);
      indices.push(prevL, prevR, l, prevR, r, l);
      prevL = l;
      prevR = r;
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.setIndex(indices);
  return geo;
}

/** 巡航青绿 → 轻刹琥珀 → 急刹红（与旧 GLSL 一致） */
function brakeColor(accel: number): THREE.Color {
  const brake = accel < 0 ? Math.min(1, -accel / HARD_BRAKE) : 0;
  const cruise = new THREE.Color(0.15, 0.78, 0.72);
  const mild = new THREE.Color(0.95, 0.72, 0.18);
  const hard = new THREE.Color(0.92, 0.18, 0.22);
  if (brake < 0.45) return cruise.clone().lerp(mild, brake / 0.45);
  return mild.clone().lerp(hard, (brake - 0.45) / 0.55);
}

/**
 * MeshBasicMaterial + vertex colors。
 * 不用 ShaderMaterial：视口优先 WebGPU，经典 GLSL（gl_FragColor）会整片变黑。
 */
export function createPlanningRibbonMaterial(): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    vertexColors: true,
    transparent: true,
    opacity: 0.82,
    depthWrite: false,
    side: THREE.DoubleSide,
    toneMapped: false,
  });
}

/**
 * 在 group 中加入 planning ribbon mesh（调用方负责 dispose）。
 * @returns 材质（兼容旧调用方），无几何则 null
 */
export function addPlanningRibbon(
  group: THREE.Group,
  samples: PlanningTrajSample[],
  vehicleWidth: number,
  z = 0.14,
): THREE.MeshBasicMaterial | null {
  const pts = downsample(samples);
  if (pts.length < 2) return null;

  const centerline = pts.map((p) => ({ x: p.x, y: p.y }));
  const accel = estimateAccel(pts);
  const halfW = Math.max(0.25, vehicleWidth / 2);

  const geo = buildMiterRibbon(centerline, halfW, accel, z);
  if (!geo) return null;

  const mat = createPlanningRibbonMaterial();
  const mesh = new THREE.Mesh(geo, mat);
  mesh.userData.pncKind = 'trajectory';
  mesh.userData.planningRibbon = true;
  group.add(mesh);
  return mat;
}

/** 保留钩子：WebGPU 兼容材质无需每帧更新 */
export function tickPlanningRibbonMaterials(_root: THREE.Object3D, _timeSec: number) {
  // no-op（原 ShaderMaterial uTime 流动感；顶点色方案在 WebGPU/WebGL 均可显示）
}
