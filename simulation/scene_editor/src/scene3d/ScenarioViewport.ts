import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js';
import type {
  AgentType,
  HdMap,
  LocationTrigger,
  RuntimeAgentState,
  Scenario,
  SelectedRef,
  ToolMode,
  TransformMode,
  TriggerType,
  Vec3,
} from '../core/types';
import {
  addPlanningRibbon,
  tickPlanningRibbonMaterials,
  type PlanningTrajSample,
} from './planningRibbonViz';
import { createDestinationFlag, tickDestinationFlag } from './destinationFlag';
import { createAgentVisual, updateAgentVisual, disposeAgentVisual, setAgentViewMode } from './agentViz';
import { nearestLanePoint, pointInLane } from '../map/types';
import { expandRouteAlongLanes, extendPathToTerminal, isPlausibleRoutePath } from '../map/routeAlongLanes';
import { sampleBezierPath } from '../core/bezierPath';
import { isAgentActive } from '../core/agentActive';
import { estimateSelectedRibbonWidth } from '../map/laneHeading';
import { fromRenderCoords, toRenderCoords } from '../apollo/coords';
import type { Vec3 as CoreVec3 } from '../core/types';
import {
  addRouteRibbon,
  addRouteTrajectoryIdle,
  addPedestrianBezierRoute,
  addRoutingPreviewPolyline,
  addRoutingWaypointGhost,
  createNumberedPin,
  ROUTE_PALETTE,
  type RoutingPreviewPoint,
} from './routeViz';
import { addMapRoads } from './mapRoadViz';

/** 移除并释放 group 中指定 kind（或全部）子对象的几何/材质，防止高频重建泄漏 */
function disposeGroupChildren(group: THREE.Group, kind?: string) {
  for (const child of [...group.children]) {
    if (kind && child.userData?.pncKind !== kind) continue;
    group.remove(child);
    child.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      mesh.geometry?.dispose?.();
      const mat = mesh.material as
        | (THREE.Material & { map?: THREE.Texture })
        | Array<THREE.Material & { map?: THREE.Texture }>
        | undefined;
      if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
      else mat?.dispose?.();
    });
  }
}

function densifyForDisplay(points: THREE.Vector3[], maxStep = 0.35): THREE.Vector3[] {
  if (points.length < 2) return points;
  const out: THREE.Vector3[] = [points[0].clone()];
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    const dist = a.distanceTo(b);
    const n = Math.max(1, Math.ceil(dist / maxStep));
    for (let k = 1; k <= n; k += 1) {
      out.push(new THREE.Vector3().lerpVectors(a, b, k / n));
    }
  }
  return out;
}

export type Backend = 'webgpu' | 'webgl';

export interface ViewportCallbacks {
  onReady?: (backend: Backend) => void;
  onSelectAgents?: (ids: string[], additive: boolean) => void;
  onSelectTrigger?: (id: string | null) => void;
  onPlace?: (point: Vec3) => void;
  onRoutePoint?: (point: Vec3, heading?: number) => void;
  onRoutingPoint?: (point: Vec3, heading?: number) => void;
  onInvalidLanePick?: () => void;
  onTransformCommit?: () => void;
  onAgentsTransformed?: (
    ids: string[],
    delta: {
      position?: Vec3;
      positions?: Record<string, Vec3>;
      heading?: number;
      scale?: number;
      sizes?: Record<string, Vec3>;
    },
  ) => void;
  onTriggerTransformed?: (
    id: string,
    patch: { center?: Vec3; size?: Vec3; heading?: number },
  ) => void;
  onHoverPoint?: (point: (Vec3 & { heading?: number }) | null) => void;
  onBezierHandleMoved?: (
    agentId: string,
    routeId: string,
    waypointId: string,
    which: 'in' | 'out',
    position: Vec3,
  ) => void;
}

const PLACE_SIZE: Record<string, Vec3> = {
  ego: { x: 1, y: 0.5, z: 0.8 },
  vehicle: { x: 1, y: 0.5, z: 0.8 },
  pedestrian: { x: 0.6, y: 0.6, z: 1.7 },
  loader: { x: 1, y: 0.5, z: 0.8 },
  static: { x: 1, y: 0.5, z: 0.8 },
  location: { x: 10, y: 4.5, z: 0.5 },
  agent_distance: { x: 4, y: 4, z: 0.2 },
};

/** routing 丝带略高于沥青路面（0.01），从车底穿过时半透明可接受 */
const ROUTE_LINE_Z = 0.08;
/** 车体中心已在 size.z/2；仅再抬离路面一点点，避免与沥青 z-fight */
const VEHICLE_CLEARANCE = 0.02;

const CORNER_LOCAL: Array<[number, number]> = [
  [1, 1],
  [1, -1],
  [-1, -1],
  [-1, 1],
];

export class ScenarioViewport {
  readonly container: HTMLElement;
  readonly scene = new THREE.Scene();
  /** Active camera: perspective (3D) or orthographic (2D top). */
  camera: THREE.PerspectiveCamera | THREE.OrthographicCamera;
  private perspectiveCamera: THREE.PerspectiveCamera;
  private orthoCamera: THREE.OrthographicCamera;
  /** Ortho half-height in world units (2D zoom). */
  private orthoHalfH = 60;
  controls: OrbitControls;
  transformControls: TransformControls;
  renderer: THREE.WebGLRenderer;
  backend: Backend = 'webgl';
  callbacks: ViewportCallbacks = {};

  private mapGroup = new THREE.Group();
  private agentGroup = new THREE.Group();
  private triggerGroup = new THREE.Group();
  private routeGroup = new THREE.Group();
  private helperGroup = new THREE.Group();
  private selectionGroup = new THREE.Group();
  private pncGroup = new THREE.Group();
  private agentMeshes = new Map<string, THREE.Group>();
  private triggerMeshes = new Map<string, THREE.Mesh>();
  private handleMeshes: THREE.Mesh[] = [];
  private animationId = 0;
  private raycaster = new THREE.Raycaster();
  private pointer = new THREE.Vector2();
  private groundPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  private ghost: THREE.Mesh | null = null;
  private pickGhost: THREE.Group | null = null;
  private routePick: {
    mode: 'route_edit' | 'routing';
    position: CoreVec3;
    heading: number;
    pointerId: number;
  } | null = null;
  private bezierHandleDrag: {
    agentId: string;
    routeId: string;
    waypointId: string;
    which: 'in' | 'out';
    pointerId: number;
  } | null = null;
  private laneSnapMarker: THREE.Mesh | null = null;
  private boxHelper: HTMLDivElement;
  private draggingBox = false;
  private boxStart = { x: 0, y: 0 };
  private transformDragging = false;
  private transformStart = new Map<
    string,
    { pos: THREE.Vector3; heading: number; size: Vec3; scale: THREE.Vector3 }
  >();
  private triggerTransformStart: {
    center: Vec3;
    size: Vec3;
    heading: number;
    pos: THREE.Vector3;
    rot: number;
    scale: THREE.Vector3;
  } | null = null;
  private resizing: {
    triggerId: string;
    corner: number;
    fixedWorld: { x: number; y: number };
    heading: number;
  } | null = null;
  private primaryId: string | null = null;
  private selectedIds: string[] = [];
  private selectedTriggerId: string | null = null;
  private toolMode: ToolMode = 'select';
  private placeType: string | null = null;
  private transformMode: TransformMode = 'translate';
  private transformActive = false;
  private scenario: Scenario | null = null;
  private map: HdMap | null = null;
  private routeRenderKey = '';
  /** Apollo routing 中心线（渲染坐标），与 dreamview routePath 同源 */
  private apolloRoutingPts: THREE.Vector3[] | null = null;
  private cameraMode: 'free' | 'top' = 'top';
  private transformingTrigger = false;

  constructor(container: HTMLElement) {
    this.container = container;
    this.container.style.position = 'relative';
    this.scene.background = new THREE.Color('#0b0d10');
    this.perspectiveCamera = new THREE.PerspectiveCamera(55, 1, 0.1, 5000);
    this.perspectiveCamera.position.set(40, -60, 55);
    this.perspectiveCamera.up.set(0, 0, 1);
    const aspect0 = Math.max(this.container.clientWidth, 1) / Math.max(this.container.clientHeight, 1);
    this.orthoCamera = new THREE.OrthographicCamera(
      -60 * aspect0,
      60 * aspect0,
      60,
      -60,
      0.1,
      5000,
    );
    // Z-up 轴对齐俯视：单位四元数，屏幕 +Y = 世界 +Y
    this.orthoCamera.up.set(0, 0, 1);
    this.orthoCamera.position.set(0, 0, 200);
    this.orthoCamera.quaternion.identity();
    // 默认 2D 正交视图
    this.camera = this.orthoCamera;

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    this.renderer.domElement.style.display = 'block';
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    container.appendChild(this.renderer.domElement);

    this.controls = this.createOrbit(this.renderer.domElement);
    this.controls.target.set(0, 0, 0);
    this.controls.enableDamping = false; // 默认 2D
    this.stabilizeOrthoTopDown();
    this.transformControls = new TransformControls(this.camera, this.renderer.domElement);
    this.transformControls.setMode('translate');
    this.transformControls.setSpace('world');
    this.transformControls.addEventListener('dragging-changed', (event) => {
      const dragging = Boolean((event as unknown as { value: boolean }).value);
      this.transformDragging = dragging;
      this.controls.enabled =
        !dragging && this.toolMode === 'select' && (this.cameraMode === 'free' || this.cameraMode === 'top');
      if (dragging) this.captureTransformStart();
      else this.commitTransform();
    });
    this.transformControls.addEventListener('objectChange', () => this.applyLiveTransform());
    this.scene.add(this.transformControls.getHelper());

    const ambient = new THREE.AmbientLight(0xffffff, 0.75);
    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(40, -30, 80);
    this.scene.add(ambient, dir);

    const grid = new THREE.GridHelper(400, 40, 0x2a3b55, 0x1a273a);
    grid.rotation.x = Math.PI / 2;
    this.scene.add(grid);
    this.scene.add(new THREE.AxesHelper(12));
    this.scene.add(
      this.mapGroup,
      this.agentGroup,
      this.triggerGroup,
      this.routeGroup,
      this.selectionGroup,
      this.helperGroup,
      this.pncGroup,
    );

    this.boxHelper = document.createElement('div');
    this.boxHelper.style.cssText =
      'position:absolute;pointer-events:none;border:1px solid #3b82f6;background:rgba(59,130,246,0.15);display:none;z-index:5;';
    container.appendChild(this.boxHelper);

    const el = this.renderer.domElement;
    el.addEventListener('pointerdown', this.onPointerDown);
    el.addEventListener('pointermove', this.onPointerMove);
    el.addEventListener('pointerup', this.onPointerUp);
    el.addEventListener('pointerleave', this.onPointerLeave);
    el.addEventListener('contextmenu', (e) => e.preventDefault());
    window.addEventListener('resize', this.handleResize);
    this.handleResize();
    this.animate();
    void this.tryUpgradeToWebGPU();
  }

  private createOrbit(dom: HTMLElement) {
    const controls = new OrbitControls(this.camera, dom);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    controls.enablePan = true;
    controls.enableZoom = true;
    const is2d = this.cameraMode === 'top';
    controls.enableRotate = !is2d;
    controls.enableDamping = !is2d;
    // Z-up：phi=0 为俯视；2D 锁俯视，3D 避开 0 奇点
    controls.minPolarAngle = is2d ? 0 : 0.05;
    controls.maxPolarAngle = is2d ? 0 : Math.PI * 0.49;
    controls.mouseButtons = {
      LEFT: is2d ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN,
    };
    controls.touches = {
      ONE: is2d ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE,
      TWO: THREE.TOUCH.DOLLY_PAN,
    };
    return controls;
  }

  private async tryUpgradeToWebGPU() {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if (!(navigator as any).gpu) {
        this.callbacks.onReady?.('webgl');
        return;
      }
      const { WebGPURenderer } = await import('three/webgpu');
      const webgpuRenderer = new WebGPURenderer({ antialias: true });
      await webgpuRenderer.init();
      webgpuRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      webgpuRenderer.setSize(this.container.clientWidth, this.container.clientHeight);
      webgpuRenderer.domElement.style.display = 'block';
      webgpuRenderer.domElement.style.width = '100%';
      webgpuRenderer.domElement.style.height = '100%';

      const old = this.renderer.domElement;
      old.removeEventListener('pointerdown', this.onPointerDown);
      old.removeEventListener('pointermove', this.onPointerMove);
      old.removeEventListener('pointerup', this.onPointerUp);
      old.removeEventListener('pointerleave', this.onPointerLeave);
      this.controls.dispose();
      this.transformControls.dispose();
      this.container.replaceChild(webgpuRenderer.domElement, old);
      this.renderer.dispose();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      this.renderer = webgpuRenderer as any;
      this.backend = 'webgpu';

      this.controls = this.createOrbit(this.renderer.domElement);
      this.applyOrbitInputMode();
      if (this.cameraMode === 'top') {
        this.stabilizeOrthoTopDown();
      } else {
        this.controls.update();
      }
      this.transformControls = new TransformControls(this.camera, this.renderer.domElement);
      this.transformControls.setMode(this.transformMode);
      this.transformControls.setSpace('world');
      this.transformControls.addEventListener('dragging-changed', (event) => {
        const dragging = Boolean((event as unknown as { value: boolean }).value);
        this.transformDragging = dragging;
        this.controls.enabled =
        !dragging && this.toolMode === 'select' && (this.cameraMode === 'free' || this.cameraMode === 'top');
        if (dragging) this.captureTransformStart();
        else this.commitTransform();
      });
      this.transformControls.addEventListener('objectChange', () => this.applyLiveTransform());
      this.scene.add(this.transformControls.getHelper());
      if (this.transformActive && this.primaryId) this.attachTransform(this.primaryId);

      const el = this.renderer.domElement;
      el.addEventListener('pointerdown', this.onPointerDown);
      el.addEventListener('pointermove', this.onPointerMove);
      el.addEventListener('pointerup', this.onPointerUp);
      el.addEventListener('pointerleave', this.onPointerLeave);
      el.addEventListener('contextmenu', (e) => e.preventDefault());
      this.callbacks.onReady?.('webgpu');
    } catch {
      this.callbacks.onReady?.('webgl');
    }
  }

  private animate = () => {
    this.animationId = requestAnimationFrame(this.animate);
    this.controls.update();
    // 2D：每帧纠正为轴对齐俯视，避免 OrbitControls 在极点 lookAt 方位角跳变
    if (this.cameraMode === 'top') {
      this.stabilizeOrthoTopDown();
    }
    this.syncSelectionHaloPositions();
    tickPlanningRibbonMaterials(this.pncGroup, performance.now() / 1000);
    tickDestinationFlag(this.routeGroup, this.camera);
    this.renderer.render(this.scene, this.camera);
  };

  /** 当前观察中心（XY 平面），2D/3D 共用，切换时保持不变 */
  private viewCenter(): THREE.Vector3 {
    return new THREE.Vector3(this.controls.target.x, this.controls.target.y, 0);
  }

  /** 由正交半高推透视距离，使画面覆盖范围接近 */
  private distanceForOrthoHalfH(halfH: number): number {
    const fov = THREE.MathUtils.degToRad(this.perspectiveCamera.fov);
    return halfH / Math.tan(fov / 2);
  }

  /** 由透视距离推正交半高 */
  private orthoHalfHForDistance(dist: number): number {
    const fov = THREE.MathUtils.degToRad(this.perspectiveCamera.fov);
    return Math.max(dist * Math.tan(fov / 2), 1);
  }

  private applyOrbitInputMode() {
    const is2d = this.cameraMode === 'top';
    this.camera.up.set(0, 0, 1);
    this.controls.enableRotate = !is2d;
    this.controls.enablePan = true;
    this.controls.enableZoom = true;
    this.controls.enabled = true;
    // 2D 关掉阻尼，避免极点附近球形插值造成「转一下」
    this.controls.enableDamping = !is2d;
    this.controls.minPolarAngle = is2d ? 0 : 0.05;
    this.controls.maxPolarAngle = is2d ? 0 : Math.PI * 0.49;
    this.controls.mouseButtons = {
      LEFT: is2d ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.PAN,
    };
    this.controls.touches = {
      ONE: is2d ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE,
      TWO: THREE.TOUCH.DOLLY_PAN,
    };
  }

  /**
   * 2D 轴对齐俯视：相机在 target 正上方，单位四元数（朝 -Z，屏幕 +Y = 世界 +Y）。
   * 不走 lookAt 极点，避免方位角跳变。
   */
  private stabilizeOrthoTopDown() {
    const t = this.controls.target;
    t.z = 0;
    this.orthoCamera.up.set(0, 0, 1);
    this.orthoCamera.position.set(t.x, t.y, 200);
    this.orthoCamera.quaternion.identity();
  }

  /** 3D 切入时的俯视位姿：与 2D 同中心、同覆盖高度，仅改投影（微偏避开 Orbit 极点）。 */
  private placePerspectiveTopDown(center: THREE.Vector3, height: number) {
    const h = Math.max(height, 1);
    // 极小偏移即可避开 polar=0 奇点，画面上几乎无位移
    const eps = Math.min(Math.max(h * 1e-4, 1e-3), 0.05);
    this.perspectiveCamera.up.set(0, 0, 1);
    this.perspectiveCamera.position.set(center.x, center.y - eps, h);
    this.controls.target.set(center.x, center.y, 0);
    this.perspectiveCamera.lookAt(center.x, center.y, 0);
    this.perspectiveCamera.updateMatrixWorld(true);
    this.perspectiveCamera.updateProjectionMatrix();
  }

  /** 切换 2D(正交) / 3D(透视)。保持观察中心与地面覆盖尺度，只做投影变换。 */
  setCameraMode(mode: 'free' | 'top') {
    if (mode === this.cameraMode && this.camera === (mode === 'top' ? this.orthoCamera : this.perspectiveCamera)) {
      return;
    }
    const center = this.viewCenter();
    this.cameraMode = mode;

    if (mode === 'top') {
      // 3D→2D：用相机高度映射正交半高，避免斜视时 distance 放大导致缩放跳动
      if (this.camera === this.perspectiveCamera) {
        const height = Math.abs(this.perspectiveCamera.position.z - center.z);
        const dist = height > 0.5 ? height : this.perspectiveCamera.position.distanceTo(center);
        if (Number.isFinite(dist) && dist > 0.1) {
          this.orthoHalfH = THREE.MathUtils.clamp(this.orthoHalfHForDistance(dist), 8, 800);
        }
      } else {
        const half = this.orthoHalfH / Math.max(this.orthoCamera.zoom, 1e-6);
        if (Number.isFinite(half) && half > 0.1) {
          this.orthoHalfH = THREE.MathUtils.clamp(half, 8, 800);
        }
      }
      this.orthoCamera.zoom = 1;
      this.updateOrthoProjection();
      this.controls.target.set(center.x, center.y, 0);
      this.camera = this.orthoCamera;
      this.controls.object = this.orthoCamera;
      this.transformControls.camera = this.orthoCamera;
      this.applyOrbitInputMode();
      this.stabilizeOrthoTopDown();
      this.controls.enableDamping = false;
      this.applyAgentViewMode('2d');
      return;
    }

    // 2D→3D：正交半高 → 透视距离，同中心俯视，再允许旋转
    const halfH = this.orthoHalfH / Math.max(this.orthoCamera.zoom, 1e-6);
    const dist = this.distanceForOrthoHalfH(halfH);
    const w = Math.max(this.container.clientWidth, 1);
    const h = Math.max(this.container.clientHeight, 1);
    this.perspectiveCamera.aspect = w / h;
    this.placePerspectiveTopDown(center, dist);
    this.camera = this.perspectiveCamera;
    this.controls.object = this.perspectiveCamera;
    this.transformControls.camera = this.perspectiveCamera;
    this.applyOrbitInputMode();
    this.controls.update();
    this.controls.enableDamping = true;
    this.applyAgentViewMode('3d');
  }

  /** 按当前视图切换 Agent 为 3D 盒子或 2D 平面 footprint */
  private applyAgentViewMode(mode: '3d' | '2d') {
    for (const [, group] of this.agentMeshes) {
      setAgentViewMode(group, mode);
    }
  }

  private updateOrthoProjection() {
    const w = Math.max(this.container.clientWidth, 1);
    const h = Math.max(this.container.clientHeight, 1);
    const aspect = w / h;
    const halfH = this.orthoHalfH;
    const halfW = halfH * aspect;
    this.orthoCamera.left = -halfW;
    this.orthoCamera.right = halfW;
    this.orthoCamera.top = halfH;
    this.orthoCamera.bottom = -halfH;
    this.orthoCamera.updateProjectionMatrix();
  }

  /** 变换拖拽时让选中光环跟随 mesh */
  private syncSelectionHaloPositions() {
    if (!this.selectionGroup.children.length) return;
    // 每个选中 Agent 对应 1 个 ring，顺序与 selectedIds 一致
    let idx = 0;
    for (const id of this.selectedIds) {
      const mesh = this.agentMeshes.get(id);
      const child = this.selectionGroup.children[idx];
      idx += 1;
      if (!mesh || !child) continue;
      child.position.x = mesh.position.x;
      child.position.y = mesh.position.y;
    }
  }

  private handleResize = () => {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    this.renderer.setSize(w, h);
    this.perspectiveCamera.aspect = w / Math.max(h, 1);
    this.perspectiveCamera.updateProjectionMatrix();
    this.updateOrthoProjection();
  };

  private setPointer(event: PointerEvent) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
  }

  private groundHit(): THREE.Vector3 | null {
    const hit = new THREE.Vector3();
    if (this.raycaster.ray.intersectPlane(this.groundPlane, hit)) return hit;
    return null;
  }

  private pickAgent(): string | null {
    const hits = this.raycaster.intersectObjects([...this.agentMeshes.values()], true);
    for (const hit of hits) {
      let obj: THREE.Object3D | null = hit.object;
      while (obj) {
        if (obj.userData.agentId) return String(obj.userData.agentId);
        obj = obj.parent;
      }
    }
    return null;
  }

  private pickTriggerHandle(): { triggerId: string; corner: number } | null {
    const hits = this.raycaster.intersectObjects(this.handleMeshes, false);
    if (!hits.length) return null;
    const ud = hits[0].object.userData;
    if (!ud.triggerId || ud.corner == null) return null;
    return { triggerId: String(ud.triggerId), corner: Number(ud.corner) };
  }

  private pickTrigger(): string | null {
    const hits = this.raycaster.intersectObjects([...this.triggerMeshes.values()], false);
    if (!hits.length) return null;
    return String(hits[0].object.userData.triggerId ?? '');
  }

  private worldCorner(trigger: LocationTrigger, corner: number) {
    const [sx, sy] = CORNER_LOCAL[corner];
    const hx = (trigger.size?.x ?? (trigger.radius ?? 6) * 2) / 2;
    const hy = (trigger.size?.y ?? (trigger.radius ?? 6) * 2) / 2;
    const lx = sx * hx;
    const ly = sy * hy;
    const c = Math.cos(trigger.heading ?? 0);
    const s = Math.sin(trigger.heading ?? 0);
    // center 存 ENU，角点与拾取均在渲染坐标
    const tc = toRenderCoords(
      { x: trigger.center.x, y: trigger.center.y, z: 0 },
      this.map,
    );
    return {
      x: tc.x + lx * c - ly * s,
      y: tc.y + lx * s + ly * c,
    };
  }

  /** 选点校验：必须在车道内，返回原始落点（不吸附中心线） */
  private validateLanePick(
    hit: THREE.Vector3,
    preferHeading?: number,
  ): { position: CoreVec3; laneHeading: number } | null {
    if (!this.map) return null;
    const point = { x: hit.x, y: hit.y, z: 0 };
    const lane = pointInLane(this.map, point, preferHeading);
    if (!lane) return null;
    return { position: point, laneHeading: lane.heading };
  }

  private routeEditAgent() {
    const id = this.primaryId ?? this.selectedIds[0] ?? null;
    if (!id || !this.scenario) return null;
    return this.scenario.agents.find((a) => a.id === id) ?? null;
  }

  private isPedestrianRouteEdit() {
    return this.toolMode === 'route_edit' && this.routeEditAgent()?.type === 'pedestrian';
  }

  private pickBezierHandle(): {
    agentId: string;
    routeId: string;
    waypointId: string;
    which: 'in' | 'out';
  } | null {
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects(this.routeGroup.children, true);
    for (const h of hits) {
      let obj: THREE.Object3D | null = h.object;
      while (obj) {
        if (obj.userData?.bezierHandle) {
          return {
            agentId: String(obj.userData.agentId),
            routeId: String(obj.userData.routeId),
            waypointId: String(obj.userData.waypointId),
            which: obj.userData.which === 'in' ? 'in' : 'out',
          };
        }
        obj = obj.parent;
      }
    }
    return null;
  }

  private getPickPreferHeading(): number {
    const agent =
      (this.primaryId && this.scenario?.agents.find((a) => a.id === this.primaryId)) ||
      this.scenario?.agents.find((a) => a.type === 'ego');
    if (!agent) return 0;
    const route = agent.routes.find((r) => r.id === agent.activeRouteId);
    const wps = route?.waypoints ?? [];
    if (wps.length >= 2) {
      const a = wps[wps.length - 2].position;
      const b = wps[wps.length - 1].position;
      const h = Math.atan2(b.y - a.y, b.x - a.x);
      if (Number.isFinite(h)) return h;
    }
    const last = wps[wps.length - 1];
    if (last?.heading != null) return last.heading;
    return agent.heading;
  }

  private ensurePickGhost(size: { x: number; y: number; z: number }) {
    if (!this.pickGhost) {
      const g = new THREE.Group();
      const body = new THREE.Mesh(
        new THREE.BoxGeometry(1, 1, 1),
        new THREE.MeshBasicMaterial({
          color: 0xcbd5e1,
          transparent: true,
          opacity: 0.2,
          depthWrite: false,
          toneMapped: false,
        }),
      );
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
        new THREE.LineBasicMaterial({
          color: 0x94a3b8,
          transparent: true,
          opacity: 0.35,
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
          opacity: 0.45,
          depthWrite: false,
          side: THREE.DoubleSide,
          toneMapped: false,
        }),
      );
      g.add(body, edges, tri);
      g.visible = false;
      this.helperGroup.add(g);
      this.pickGhost = g;
    }
    this.pickGhost.scale.set(size.x, size.y, Math.max(0.35, size.z * 0.85));
  }

  private updatePickGhost(pos: CoreVec3, heading: number, visible: boolean) {
    const agent =
      (this.primaryId && this.scenario?.agents.find((a) => a.id === this.primaryId)) ||
      this.scenario?.agents.find((a) => a.type === 'ego');
    const size = agent?.size ?? PLACE_SIZE.ego;
    this.ensurePickGhost(size);
    if (!this.pickGhost) return;
    this.pickGhost.visible = visible;
    if (!visible) return;
    this.pickGhost.position.set(pos.x, pos.y, size.z / 2 + 0.02);
    this.pickGhost.rotation.z = heading;
    // routing 模式悬停虚影略加强，便于辨认朝向
    if (this.toolMode === 'routing') {
      const body = this.pickGhost.children[0] as THREE.Mesh;
      const mat = body.material as THREE.MeshBasicMaterial;
      mat.opacity = 0.32;
    }
  }

  private clearPickGhost() {
    if (this.pickGhost) this.pickGhost.visible = false;
  }

  private updateLaneSnapMarker(pos: CoreVec3 | null, valid: boolean) {
    if (!this.laneSnapMarker) {
      this.laneSnapMarker = new THREE.Mesh(
        new THREE.SphereGeometry(0.28, 16, 16),
        new THREE.MeshBasicMaterial({ color: 0x22c55e, depthTest: false, transparent: true, opacity: 0.7 }),
      );
      this.laneSnapMarker.renderOrder = 20;
      this.helperGroup.add(this.laneSnapMarker);
    }
    if (!pos) {
      this.laneSnapMarker.visible = false;
      return;
    }
    this.laneSnapMarker.visible = true;
    this.laneSnapMarker.position.set(pos.x, pos.y, 0.12);
    (this.laneSnapMarker.material as THREE.MeshBasicMaterial).color.set(
      valid ? 0x22c55e : 0xef4444,
    );
  }

  private onPointerDown = (event: PointerEvent) => {
    if (event.button === 2 || event.button === 1) return; // pan / zoom via orbit
    if (this.transformDragging) return;
    this.setPointer(event);

    if (this.toolMode === 'place') {
      const hit = this.groundHit();
      if (hit) this.callbacks.onPlace?.({ x: hit.x, y: hit.y, z: 0 });
      return;
    }
    if (this.toolMode === 'route_edit' || this.toolMode === 'routing') {
      const hit = this.groundHit();
      if (!hit) return;

      if (this.toolMode === 'route_edit') {
        const handle = this.pickBezierHandle();
        if (handle) {
          this.bezierHandleDrag = { ...handle, pointerId: event.pointerId };
          this.controls.enabled = false;
          try {
            this.renderer.domElement.setPointerCapture(event.pointerId);
          } catch {
            /* ignore */
          }
          return;
        }
      }

      const prefer = this.getPickPreferHeading();
      if (this.isPedestrianRouteEdit()) {
        this.routePick = {
          mode: 'route_edit',
          position: { x: hit.x, y: hit.y, z: 0 },
          heading: prefer,
          pointerId: event.pointerId,
        };
        this.controls.enabled = false;
        this.updateLaneSnapMarker(null, false);
        this.updatePickGhost({ x: hit.x, y: hit.y, z: 0 }, prefer, true);
        try {
          this.renderer.domElement.setPointerCapture(event.pointerId);
        } catch {
          /* ignore */
        }
        return;
      }

      const picked = this.validateLanePick(hit, prefer);
      if (!picked) {
        this.callbacks.onInvalidLanePick?.();
        return;
      }
      this.routePick = {
        mode: this.toolMode,
        position: picked.position,
        heading: picked.laneHeading,
        pointerId: event.pointerId,
      };
      this.controls.enabled = false;
      this.updateLaneSnapMarker(null, false);
      this.updatePickGhost(picked.position, picked.laneHeading, true);
      try {
        this.renderer.domElement.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
      return;
    }

    const handle = this.pickTriggerHandle();
    if (handle && this.scenario) {
      const trigger = this.scenario.triggers.find(
        (t) => t.id === handle.triggerId && t.type === 'location',
      ) as LocationTrigger | undefined;
      if (trigger) {
        const opposite = (handle.corner + 2) % 4;
        this.resizing = {
          triggerId: handle.triggerId,
          corner: handle.corner,
          fixedWorld: this.worldCorner(trigger, opposite),
          heading: trigger.heading ?? 0,
        };
        this.controls.enabled = false;
        this.callbacks.onSelectTrigger?.(handle.triggerId);
        return;
      }
    }

    const triggerId = this.pickTrigger();
    if (triggerId) {
      this.callbacks.onSelectTrigger?.(triggerId);
      return;
    }

    const agentId = this.pickAgent();
    if (agentId) {
      this.callbacks.onSelectAgents?.([agentId], event.shiftKey);
      return;
    }

    // start box select
    this.draggingBox = true;
    this.controls.enabled = false;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.boxStart = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    this.boxHelper.style.display = 'block';
    this.boxHelper.style.left = `${this.boxStart.x}px`;
    this.boxHelper.style.top = `${this.boxStart.y}px`;
    this.boxHelper.style.width = '0px';
    this.boxHelper.style.height = '0px';
  };

  private onPointerMove = (event: PointerEvent) => {
    this.setPointer(event);
    const hit = this.groundHit();

    if (this.bezierHandleDrag) {
      if (hit) {
        this.callbacks.onBezierHandleMoved?.(
          this.bezierHandleDrag.agentId,
          this.bezierHandleDrag.routeId,
          this.bezierHandleDrag.waypointId,
          this.bezierHandleDrag.which,
          { x: hit.x, y: hit.y, z: 0 },
        );
      }
      return;
    }

    if (hit) {
      if ((this.toolMode === 'routing' || this.toolMode === 'route_edit') && this.map) {
        if (this.isPedestrianRouteEdit()) {
          const prefer = this.getPickPreferHeading();
          this.callbacks.onHoverPoint?.({
            x: hit.x,
            y: hit.y,
            z: 0,
            heading: prefer,
          });
          this.updateLaneSnapMarker(null, false);
          if (!this.routePick) {
            this.updatePickGhost({ x: hit.x, y: hit.y, z: 0 }, prefer, true);
          }
        } else {
          const prefer = this.getPickPreferHeading();
          const inLane = pointInLane(this.map, { x: hit.x, y: hit.y, z: 0 }, prefer);
          this.callbacks.onHoverPoint?.(
            inLane
              ? { x: hit.x, y: hit.y, z: 0, heading: inLane.heading }
              : { x: hit.x, y: hit.y, z: 0 },
          );
        }
      } else {
        this.callbacks.onHoverPoint?.({ x: hit.x, y: hit.y, z: 0 });
      }
    } else {
      this.callbacks.onHoverPoint?.(null);
    }

    if (this.routePick) {
      if (hit) {
        const dx = hit.x - this.routePick.position.x;
        const dy = hit.y - this.routePick.position.y;
        if (Math.hypot(dx, dy) > 0.12) {
          this.routePick.heading = Math.atan2(dy, dx);
        }
        this.updatePickGhost(this.routePick.position, this.routePick.heading, true);
      }
      return;
    }

    if (this.toolMode === 'route_edit' || this.toolMode === 'routing') {
      if (this.isPedestrianRouteEdit()) {
        // hover ghost already updated above
      } else if (hit && this.map) {
        const prefer = this.getPickPreferHeading();
        const point = { x: hit.x, y: hit.y, z: 0 };
        const inLane = pointInLane(this.map, point, prefer);
        if (inLane) {
          this.updateLaneSnapMarker(point, true);
          this.updatePickGhost(point, inLane.heading, true);
        } else {
          const near = nearestLanePoint(this.map, point, prefer);
          this.updateLaneSnapMarker(point, false);
          this.clearPickGhost();
          if (!near) this.updateLaneSnapMarker(null, false);
        }
      } else {
        this.updateLaneSnapMarker(null, false);
        this.clearPickGhost();
      }
    } else {
      this.updateLaneSnapMarker(null, false);
      this.clearPickGhost();
    }

    if (this.resizing && hit) {
      const { fixedWorld, heading, triggerId } = this.resizing;
      const c = Math.cos(-heading);
      const s = Math.sin(-heading);
      const dx = hit.x - fixedWorld.x;
      const dy = hit.y - fixedWorld.y;
      const localX = dx * c - dy * s;
      const localY = dx * s + dy * c;
      const length = Math.max(1, Math.abs(localX));
      const width = Math.max(1, Math.abs(localY));
      const midLocalX = localX / 2;
      const midLocalY = localY / 2;
      const cw = Math.cos(heading);
      const sw = Math.sin(heading);
      const center = {
        x: fixedWorld.x + midLocalX * cw - midLocalY * sw,
        y: fixedWorld.y + midLocalX * sw + midLocalY * cw,
        z: 0,
      };
      this.callbacks.onTriggerTransformed?.(triggerId, {
        center: fromRenderCoords(center, this.map),
        size: { x: length, y: width, z: 0.5 },
      });
      return;
    }

    if (this.toolMode === 'place' && hit) {
      this.updateGhost(hit);
    }

    if (!this.draggingBox) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const left = Math.min(this.boxStart.x, x);
    const top = Math.min(this.boxStart.y, y);
    const width = Math.abs(x - this.boxStart.x);
    const height = Math.abs(y - this.boxStart.y);
    this.boxHelper.style.left = `${left}px`;
    this.boxHelper.style.top = `${top}px`;
    this.boxHelper.style.width = `${width}px`;
    this.boxHelper.style.height = `${height}px`;
  };

  private finishRoutePick() {
    const pick = this.routePick;
    if (!pick) return;
    this.routePick = null;
    this.controls.enabled =
      this.toolMode === 'select' && !this.transformDragging && !this.resizing;
    this.clearPickGhost();
    if (pick.mode === 'route_edit') {
      this.callbacks.onRoutePoint?.(pick.position, pick.heading);
    } else {
      this.callbacks.onRoutingPoint?.(pick.position, pick.heading);
    }
  }

  private onPointerUp = (event: PointerEvent) => {
    if (this.bezierHandleDrag) {
      try {
        this.renderer.domElement.releasePointerCapture(this.bezierHandleDrag.pointerId);
      } catch {
        /* ignore */
      }
      this.bezierHandleDrag = null;
      this.controls.enabled =
        this.toolMode === 'select' && !this.transformDragging && !this.resizing;
      return;
    }

    if (this.routePick) {
      try {
        this.renderer.domElement.releasePointerCapture(this.routePick.pointerId);
      } catch {
        /* ignore */
      }
      this.finishRoutePick();
      return;
    }

    if (this.resizing) {
      this.resizing = null;
      this.controls.enabled = this.toolMode === 'select';
      return;
    }

    if (!this.draggingBox) return;
    this.draggingBox = false;
    this.controls.enabled = this.toolMode === 'select';
    const rect = this.renderer.domElement.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const left = Math.min(this.boxStart.x, x);
    const top = Math.min(this.boxStart.y, y);
    const right = Math.max(this.boxStart.x, x);
    const bottom = Math.max(this.boxStart.y, y);
    this.boxHelper.style.display = 'none';

    if (right - left < 4 && bottom - top < 4) {
      // click empty -> clear unless shift
      if (!event.shiftKey) {
        this.callbacks.onSelectAgents?.([], false);
        this.callbacks.onSelectTrigger?.(null);
      }
      return;
    }

    const ids: string[] = [];
    for (const [id, mesh] of this.agentMeshes) {
      const projected = mesh.position.clone().project(this.camera);
      const sx = ((projected.x + 1) / 2) * rect.width;
      const sy = ((-projected.y + 1) / 2) * rect.height;
      if (sx >= left && sx <= right && sy >= top && sy <= bottom) ids.push(id);
    }
    this.callbacks.onSelectAgents?.(ids, event.shiftKey);
  };

  private onPointerLeave = () => {
    if (this.routePick) return;
    if (this.toolMode === 'place') this.clearGhost();
    this.updateLaneSnapMarker(null, false);
    this.clearPickGhost();
    this.callbacks.onHoverPoint?.(null);
  };

  private updateGhost(hit: THREE.Vector3) {
    const size = PLACE_SIZE[this.placeType ?? 'vehicle'] ?? PLACE_SIZE.vehicle;
    const isArea = this.placeType === 'location';
    const isEgo = this.placeType === 'ego';
    if (!this.ghost) {
      this.ghost = new THREE.Mesh(
        new THREE.BoxGeometry(1, 1, 1),
        new THREE.MeshStandardMaterial({
          color: isArea ? 0xa855f7 : isEgo ? 0xe74c3c : 0x38bdf8,
          transparent: true,
          opacity: 0.4,
          depthWrite: false,
        }),
      );
      this.helperGroup.add(this.ghost);
    }
    const mat = this.ghost.material as THREE.MeshStandardMaterial;
    mat.color.set(isArea ? 0xa855f7 : isEgo ? 0xe74c3c : 0x38bdf8);
    this.ghost.visible = true;
    this.ghost.scale.set(size.x, size.y, size.z);
    this.ghost.position.set(hit.x, hit.y, isArea ? 0.05 : size.z / 2);
  }

  private clearGhost() {
    if (this.ghost) this.ghost.visible = false;
  }

  setInteractionState(opts: {
    toolMode: ToolMode;
    placeType: string | null;
    transformMode: TransformMode;
    transformActive: boolean;
    selectedIds: string[];
    selected: SelectedRef;
  }) {
    this.toolMode = opts.toolMode;
    this.placeType = opts.placeType;
    this.transformMode = opts.transformMode;
    this.transformActive = opts.transformActive;
    this.selectedIds = opts.selectedIds;
    this.selectedTriggerId = opts.selected?.kind === 'trigger' ? opts.selected.id : null;
    this.primaryId =
      opts.selected?.kind === 'agent'
        ? opts.selected.id
        : opts.selectedIds[opts.selectedIds.length - 1] ?? null;

    this.controls.enabled =
      opts.toolMode === 'select' && !this.transformDragging && !this.resizing;

    if (opts.transformActive && opts.toolMode === 'select') {
      this.transformControls.setMode(opts.transformMode);
      this.transformControls.setSpace(opts.transformMode === 'scale' ? 'local' : 'world');
      if (this.selectedTriggerId) {
        this.transformingTrigger = true;
        this.attachTriggerTransform(this.selectedTriggerId);
      } else if (this.primaryId) {
        this.transformingTrigger = false;
        this.attachTransform(this.primaryId);
      } else {
        this.transformingTrigger = false;
        this.transformControls.detach();
      }
    } else {
      this.transformingTrigger = false;
      this.transformControls.detach();
    }

    if (opts.toolMode !== 'place') this.clearGhost();
    if (opts.toolMode !== 'route_edit' && opts.toolMode !== 'routing') {
      this.routePick = null;
      this.updateLaneSnapMarker(null, false);
      this.clearPickGhost();
    }
  }

  private attachTransform(agentId: string) {
    const mesh = this.agentMeshes.get(agentId);
    if (!mesh) {
      this.transformControls.detach();
      return;
    }
    this.transformControls.attach(mesh);
  }

  private attachTriggerTransform(triggerId: string) {
    const mesh = this.triggerMeshes.get(triggerId);
    if (!mesh) {
      this.transformControls.detach();
      return;
    }
    this.transformControls.attach(mesh);
  }

  private captureTransformStart() {
    this.transformStart.clear();
    this.triggerTransformStart = null;

    if (this.transformingTrigger && this.selectedTriggerId && this.scenario) {
      const trigger = this.scenario.triggers.find(
        (t) => t.id === this.selectedTriggerId && t.type === 'location',
      ) as LocationTrigger | undefined;
      const mesh = this.triggerMeshes.get(this.selectedTriggerId);
      if (trigger && mesh) {
        this.triggerTransformStart = {
          center: { ...trigger.center },
          size: { ...(trigger.size ?? { x: 10, y: 4.5, z: 0.5 }) },
          heading: trigger.heading ?? 0,
          pos: mesh.position.clone(),
          rot: mesh.rotation.z,
          scale: mesh.scale.clone(),
        };
      }
      return;
    }

    if (!this.scenario) return;
    for (const id of this.selectedIds.length ? this.selectedIds : this.primaryId ? [this.primaryId] : []) {
      const agent = this.scenario.agents.find((a) => a.id === id);
      const mesh = this.agentMeshes.get(id);
      if (!agent || !mesh) continue;
      this.transformStart.set(id, {
        pos: mesh.position.clone(),
        heading: mesh.rotation.z,
        size: { ...agent.size },
        scale: mesh.scale.clone(),
      });
    }
  }

  private applyLiveTransform() {
    if (this.transformingTrigger) return;
    if (!this.primaryId || !this.scenario) return;
    const primaryMesh = this.agentMeshes.get(this.primaryId);
    const start = this.transformStart.get(this.primaryId);
    if (!primaryMesh || !start) return;

    if (this.transformMode === 'translate') {
      const dx = primaryMesh.position.x - start.pos.x;
      const dy = primaryMesh.position.y - start.pos.y;
      const dz = primaryMesh.position.z - start.pos.z;
      for (const [id, s] of this.transformStart) {
        if (id === this.primaryId) continue;
        const mesh = this.agentMeshes.get(id);
        if (!mesh) continue;
        mesh.position.set(s.pos.x + dx, s.pos.y + dy, s.pos.z + dz);
      }
    } else if (this.transformMode === 'rotate') {
      const delta = primaryMesh.rotation.z - start.heading;
      for (const [id, s] of this.transformStart) {
        if (id === this.primaryId) continue;
        const mesh = this.agentMeshes.get(id);
        if (!mesh) continue;
        mesh.rotation.z = s.heading + delta;
      }
    } else if (this.transformMode === 'scale') {
      const fx = primaryMesh.scale.x / Math.max(start.scale.x, 0.001);
      const fy = primaryMesh.scale.y / Math.max(start.scale.y, 0.001);
      const fz = primaryMesh.scale.z / Math.max(start.scale.z, 0.001);
      for (const [id, s] of this.transformStart) {
        if (id === this.primaryId) continue;
        const mesh = this.agentMeshes.get(id);
        if (!mesh) continue;
        mesh.scale.set(s.scale.x * fx, s.scale.y * fy, s.scale.z * fz);
        mesh.position.z = Math.max(0.05, mesh.scale.z / 2);
      }
      primaryMesh.position.z = Math.max(0.05, primaryMesh.scale.z / 2);
    }
  }

  private commitTransform() {
    if (this.transformingTrigger && this.selectedTriggerId && this.triggerTransformStart) {
      const mesh = this.triggerMeshes.get(this.selectedTriggerId);
      const start = this.triggerTransformStart;
      if (!mesh) return;
      if (this.transformMode === 'translate') {
        this.callbacks.onTriggerTransformed?.(this.selectedTriggerId, {
          center: fromRenderCoords(
            { x: mesh.position.x, y: mesh.position.y, z: 0 },
            this.map,
          ),
        });
      } else if (this.transformMode === 'rotate') {
        this.callbacks.onTriggerTransformed?.(this.selectedTriggerId, {
          heading: start.heading + (mesh.rotation.z - start.rot),
        });
      } else if (this.transformMode === 'scale') {
        this.callbacks.onTriggerTransformed?.(this.selectedTriggerId, {
          size: {
            x: Math.max(1, start.size.x * (mesh.scale.x / Math.max(start.scale.x, 0.001))),
            y: Math.max(1, start.size.y * (mesh.scale.y / Math.max(start.scale.y, 0.001))),
            z: Math.max(0.2, start.size.z * (mesh.scale.z / Math.max(start.scale.z, 0.001))),
          },
        });
      }
      this.triggerTransformStart = null;
      this.callbacks.onTransformCommit?.();
      return;
    }

    if (!this.primaryId) return;
    const primaryMesh = this.agentMeshes.get(this.primaryId);
    const start = this.transformStart.get(this.primaryId);
    if (!primaryMesh || !start) return;
    const ids = [...this.transformStart.keys()];

    if (this.transformMode === 'translate') {
      const positions: Record<string, Vec3> = {};
      for (const id of ids) {
        const mesh = this.agentMeshes.get(id);
        const agent = this.scenario?.agents.find((a) => a.id === id);
        if (!mesh || !agent) continue;
        const ap = fromRenderCoords(
          { x: mesh.position.x, y: mesh.position.y, z: 0 },
          this.map,
        );
        positions[id] = { x: ap.x, y: ap.y, z: agent.position.z };
      }
      this.callbacks.onAgentsTransformed?.(ids, { positions });
    } else if (this.transformMode === 'rotate') {
      this.callbacks.onAgentsTransformed?.(ids, {
        heading: primaryMesh.rotation.z - start.heading,
      });
    } else if (this.transformMode === 'scale') {
      const sizes: Record<string, Vec3> = {};
      for (const id of ids) {
        const mesh = this.agentMeshes.get(id);
        if (!mesh) continue;
        sizes[id] = {
          x: Math.max(0.1, mesh.scale.x),
          y: Math.max(0.1, mesh.scale.y),
          z: Math.max(0.1, mesh.scale.z),
        };
      }
      this.callbacks.onAgentsTransformed?.(ids, { sizes });
    }
    this.callbacks.onTransformCommit?.();
    this.transformStart.clear();
  }

  /**
   * Apollo planning 轨迹：等宽 ribbon + 刹车着色（不含终点旗；旗在 routing 终点）。
   */
  setPlanningTrajectory(
    points: PlanningTrajSample[] | null,
    vehicleWidth = 1.0,
  ) {
    disposeGroupChildren(this.pncGroup, 'trajectory');
    if (!points || points.length < 2) return;
    addPlanningRibbon(this.pncGroup, points, vehicleWidth, 0.14);
  }

  /** Prediction 障碍物预测轨迹（仅仿真中显示） */
  setPredictionTrajectories(
    items: Array<{ id: number | string; points: Array<{ x: number; y: number }> }> | null,
  ) {
    disposeGroupChildren(this.pncGroup, 'prediction');
    if (!items || items.length === 0) return;
    for (const item of items) {
      if (item.points.length < 2) continue;
      const step = Math.max(1, Math.floor(item.points.length / 200));
      const sampled =
        step > 1
          ? item.points.filter((_, i) => i % step === 0 || i === item.points.length - 1)
          : item.points;
      const pts = sampled.map((p) => new THREE.Vector3(p.x, p.y, 0.25));
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({
          color: 0xf59e0b,
          transparent: true,
          opacity: 0.9,
        }),
      );
      line.userData.pncKind = 'prediction';
      this.pncGroup.add(line);
    }
  }

  setRoutingPreview(
    points: RoutingPreviewPoint[],
    hover?: RoutingPreviewPoint | null,
  ) {
    disposeGroupChildren(this.pncGroup, 'routing');
    const ego = this.scenario?.agents.find((a) => a.type === 'ego');
    const size = ego?.size ?? PLACE_SIZE.ego;
    const linePts: THREE.Vector3[] = [];

    points.forEach((p) => {
      addRoutingWaypointGhost(this.pncGroup, p, size, 'placed');
      linePts.push(new THREE.Vector3(p.x, p.y));
    });

    if (hover) {
      addRoutingWaypointGhost(this.pncGroup, hover, size, 'hover');
      if (linePts.length > 0) {
        linePts.push(new THREE.Vector3(hover.x, hover.y));
      }
    }

    addRoutingPreviewPolyline(this.pncGroup, linePts);
  }

  /** Apollo routing 路径（渲染坐标）；有值时 Ego 活动 Route 优先用此绘制 */
  setApolloRoutingPath(points: CoreVec3[] | null) {
    this.apolloRoutingPts =
      points && points.length >= 2
        ? points.map((p) => new THREE.Vector3(p.x, p.y, ROUTE_LINE_Z))
        : null;
    this.routeRenderKey = '';
  }

  fitToMap(map: HdMap) {
    const cx = (map.bounds.min.x + map.bounds.max.x) / 2;
    const cy = (map.bounds.min.y + map.bounds.max.y) / 2;
    const span = Math.max(
      map.bounds.max.x - map.bounds.min.x,
      map.bounds.max.y - map.bounds.min.y,
      40,
    );
    this.controls.target.set(cx, cy, 0);
    if (this.cameraMode === 'top') {
      this.orthoHalfH = span * 0.55;
      this.orthoCamera.zoom = 1;
      this.updateOrthoProjection();
      this.controls.object = this.orthoCamera;
      this.applyOrbitInputMode();
      this.stabilizeOrthoTopDown();
      return;
    }
    const dist = span * 0.9;
    this.placePerspectiveTopDown(new THREE.Vector3(cx, cy, 0), dist);
    this.controls.object = this.perspectiveCamera;
    this.applyOrbitInputMode();
    this.controls.update();
  }

  setMap(map: HdMap | null, layers: { lanes: boolean; boundaries: boolean; nodes: boolean }) {
    this.map = map;
    disposeGroupChildren(this.mapGroup);
    if (!map) return;

    // 沥青路面 + 白边 + 中心虚线 + 白色直行/左转/右转地面标识（参考 lane demo）
    addMapRoads(this.mapGroup, map, { lanes: layers.lanes, boundaries: layers.boundaries });

    if (layers.nodes) {
      for (const node of map.nodes) {
        const color =
          node.kind === 'load'
            ? 0x22c55e
            : node.kind === 'unload'
              ? 0xf97316
              : node.kind === 'pass'
                ? 0xeab308
                : 0x38bdf8;
        const mesh = new THREE.Mesh(
          new THREE.SphereGeometry(0.8, 12, 12),
          new THREE.MeshStandardMaterial({ color }),
        );
        mesh.position.set(node.position.x, node.position.y, 0.8);
        mesh.userData.mapKind = 'node';
        this.mapGroup.add(mesh);
      }
    }
  }

  syncScenario(
    scenario: Scenario,
    runtime: Record<string, RuntimeAgentState>,
    selectedIds: string[],
    selectedTriggerId: string | null = null,
    simRunning = false,
  ) {
    this.scenario = scenario;
    this.selectedIds = selectedIds;
    this.selectedTriggerId = selectedTriggerId;
    const liveIds = new Set(scenario.agents.map((a) => a.id));
    for (const [id, mesh] of this.agentMeshes) {
      if (!liveIds.has(id)) {
        this.agentGroup.remove(mesh);
        disposeAgentVisual(mesh);
        this.agentMeshes.delete(id);
      }
    }

    for (const agent of scenario.agents) {
      let group = this.agentMeshes.get(agent.id);
      if (!group) {
        group = createAgentVisual(agent.type, agent.color);
        group.userData.agentId = agent.id;
        this.agentMeshes.set(agent.id, group);
        this.agentGroup.add(group);
      }

      if (this.transformDragging && this.transformStart.has(agent.id)) {
        // live transform owns mesh pose
      } else {
        const rt = runtime[agent.id];
        const posRaw = simRunning && rt?.position ? rt.position : agent.position;
        const pos = toRenderCoords(posRaw, this.map);
        const heading = rt?.heading ?? agent.heading;
        const is2d = this.cameraMode === 'top';
        if (is2d) {
          // 2D：只保留长宽 footprint，贴地
          group.scale.set(agent.size.x, agent.size.y, 1);
          group.position.set(pos.x, pos.y, 0.05);
        } else {
          group.scale.set(agent.size.x, agent.size.y, agent.size.z);
          group.position.set(pos.x, pos.y, pos.z + VEHICLE_CLEARANCE);
        }
        group.rotation.z = heading;
        setAgentViewMode(group, is2d ? '2d' : '3d');
      }

      const rt = runtime[agent.id];
      const isActive = isAgentActive(agent, rt);
      updateAgentVisual(
        group,
        agent.color,
        selectedIds.includes(agent.id),
        isActive,
      );
    }

    // 选中光环：单圈小号绿色环，贴地跟随
    disposeGroupChildren(this.selectionGroup);
    for (const id of selectedIds) {
      const agent = scenario.agents.find((a) => a.id === id);
      const mesh = this.agentMeshes.get(id);
      if (!agent || !mesh) continue;
      const halfDiag =
        Math.hypot(Math.max(agent.size.x, 0.2), Math.max(agent.size.y, 0.2)) * 0.5;
      const inner = halfDiag + 0.12;
      const outerR = inner + 0.1;
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(inner, outerR, 48),
        new THREE.MeshBasicMaterial({
          color: 0x22c55e,
          transparent: true,
          opacity: 0.95,
          depthWrite: false,
          side: THREE.DoubleSide,
          toneMapped: false,
        }),
      );
      ring.position.set(mesh.position.x, mesh.position.y, 0.06);
      ring.renderOrder = 3;
      this.selectionGroup.add(ring);
    }

    // triggers（变换拖拽中不重建，避免打断 Gizmo）
    if (!(this.transformDragging && this.transformingTrigger)) {
      this.triggerGroup.clear();
      this.triggerMeshes.clear();
      this.handleMeshes = [];
      for (const trigger of scenario.triggers) {
        if (trigger.type !== 'location') continue;
        const size = trigger.size ?? {
          x: (trigger.radius ?? 6) * 2,
          y: (trigger.radius ?? 6) * 2,
          z: 0.5,
        };
        const selected = selectedTriggerId === trigger.id;
        const mesh = new THREE.Mesh(
          new THREE.BoxGeometry(1, 1, 1),
          new THREE.MeshStandardMaterial({
            color: trigger.fired ? 0x22c55e : 0xa855f7,
            transparent: true,
            opacity: selected ? 0.55 : 0.38,
            depthWrite: false,
            side: THREE.DoubleSide,
          }),
        );
        mesh.userData.triggerId = trigger.id;
        mesh.scale.set(size.x, size.y, size.z);
        const tc = toRenderCoords(
          { x: trigger.center.x, y: trigger.center.y, z: 0 },
          this.map,
        );
        mesh.position.set(tc.x, tc.y, size.z / 2 + 0.02);
        mesh.rotation.z = trigger.heading ?? 0;
        this.triggerMeshes.set(trigger.id, mesh);
        this.triggerGroup.add(mesh);

        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(new THREE.BoxGeometry(size.x, size.y, size.z)),
          new THREE.LineBasicMaterial({
            color: selected ? 0xe9d5ff : 0x7e22ce,
            transparent: true,
            opacity: 0.9,
          }),
        );
        edges.position.copy(mesh.position);
        edges.rotation.z = mesh.rotation.z;
        this.triggerGroup.add(edges);

        if (selected) {
          for (let i = 0; i < 4; i++) {
            const [sx, sy] = CORNER_LOCAL[i];
            const hx = size.x / 2;
            const hy = size.y / 2;
            const lx = sx * hx;
            const ly = sy * hy;
            const c = Math.cos(trigger.heading ?? 0);
            const s = Math.sin(trigger.heading ?? 0);
            const handle = new THREE.Mesh(
              new THREE.SphereGeometry(0.55, 14, 14),
              new THREE.MeshBasicMaterial({ color: 0xfacc15 }),
            );
            handle.position.set(
              tc.x + lx * c - ly * s,
              tc.y + lx * s + ly * c,
              size.z + 0.15,
            );
            handle.userData.triggerId = trigger.id;
            handle.userData.corner = i;
            this.handleMeshes.push(handle);
            this.triggerGroup.add(handle);
          }
        }
      }
    }

    // routes — 选中 Agent：绘制其全部 Route（多色丝带，对标 183118 多路径切换）
    // 未选中：仅 activeRoute 细线。终点旗挂在每条 Route 末路点（routing 终点）。
    const routeRenderKey = JSON.stringify({
      map: this.map?.id ?? null,
      selectedIds,
      apolloRouting: this.apolloRoutingPts?.map((p) => [p.x.toFixed(2), p.y.toFixed(2)]) ?? null,
      routes: scenario.agents.map((a) => ({
        id: a.id,
        type: a.type,
        en: a.enabled !== false,
        rtEn: runtime[a.id]?.enabled,
        rtMv: runtime[a.id]?.moving,
        rid: a.activeRouteId,
        pos: [a.position.x.toFixed(2), a.position.y.toFixed(2), a.heading.toFixed(3)],
        all: a.routes.map((r) => ({
          id: r.id,
          pt: r.pathType ?? '',
          wps: r.waypoints.map((w) => [
            w.position.x.toFixed(2),
            w.position.y.toFixed(2),
            w.handleIn?.x.toFixed(2) ?? '',
            w.handleIn?.y.toFixed(2) ?? '',
            w.handleOut?.x.toFixed(2) ?? '',
            w.handleOut?.y.toFixed(2) ?? '',
          ]),
        })),
      })),
    });
    if (routeRenderKey !== this.routeRenderKey) {
      this.routeRenderKey = routeRenderKey;
      disposeGroupChildren(this.routeGroup);
      for (const agent of scenario.agents) {
        const isSelected = selectedIds.includes(agent.id);
        const isActive = isAgentActive(agent, runtime[agent.id]);
        // Disable 且未选中：不画路径（未激活，无预测轨迹感）
        if (!isActive && !isSelected) continue;
        const routesToDraw = isSelected
          ? agent.routes
          : agent.routes.filter((r) => r.id === agent.activeRouteId);

        routesToDraw.forEach((route) => {
          if (route.waypoints.length < 1) return;
          const terminalRender = toRenderCoords(
            route.waypoints[route.waypoints.length - 1].position,
            this.map,
          );

          const isPedBezier =
            agent.type === 'pedestrian' || route.pathType === 'bezier';
          if (isPedBezier) {
            const sample = sampleBezierPath(
              route.waypoints.map((w) => ({
                ...w,
                position: toRenderCoords(w.position, this.map),
                handleIn: w.handleIn
                  ? toRenderCoords(w.handleIn, this.map)
                  : undefined,
                handleOut: w.handleOut
                  ? toRenderCoords(w.handleOut, this.map)
                  : undefined,
              })),
              0.25,
            );
            const handles =
              isSelected
                ? route.waypoints.flatMap((w) => {
                    const anchor = toRenderCoords(w.position, this.map);
                    const list: Array<{
                      waypointId: string;
                      which: 'in' | 'out';
                      x: number;
                      y: number;
                      ax: number;
                      ay: number;
                    }> = [];
                    if (w.handleIn) {
                      const h = toRenderCoords(w.handleIn, this.map);
                      list.push({
                        waypointId: w.id,
                        which: 'in',
                        x: h.x,
                        y: h.y,
                        ax: anchor.x,
                        ay: anchor.y,
                      });
                    }
                    if (w.handleOut) {
                      const h = toRenderCoords(w.handleOut, this.map);
                      list.push({
                        waypointId: w.id,
                        which: 'out',
                        x: h.x,
                        y: h.y,
                        ax: anchor.x,
                        ay: anchor.y,
                      });
                    }
                    return list;
                  })
                : [];
            addPedestrianBezierRoute(this.routeGroup, {
              samplePts: sample.map((p) => new THREE.Vector3(p.x, p.y, ROUTE_LINE_Z)),
              anchors: route.waypoints.map((w) => {
                const p = toRenderCoords(w.position, this.map);
                return { id: w.id, x: p.x, y: p.y };
              }),
              handles,
              agentId: agent.id,
              routeId: route.id,
              selected: isSelected,
              z: ROUTE_LINE_Z,
            });
            if (isSelected && route.waypoints.length >= 1) {
              const flag = createDestinationFlag();
              flag.position.set(terminalRender.x, terminalRender.y, 0);
              this.routeGroup.add(flag);
            }
            return;
          }

          const paletteIdx = Math.max(
            0,
            agent.routes.findIndex((r) => r.id === route.id),
          );
          // 一段 Route 固定一色（按创建顺序），不随 active 切换改色
          const routeColor = ROUTE_PALETTE[paletteIdx % ROUTE_PALETTE.length];
          const isActive = route.id === agent.activeRouteId;

          // 活动 Ego Route：主车若已偏离首路点，从当前位置起笔以便路径连贯
          const origin = toRenderCoords(agent.position, this.map);
          const pathWaypoints = (() => {
            const rest = route.waypoints.map((w) => {
              const p = toRenderCoords(w.position, this.map);
              return {
                x: p.x,
                y: p.y,
                z: p.z,
                heading: w.heading,
              };
            });
            const prependActiveEgo =
              agent.type === 'ego' &&
              isActive &&
              rest.length > 0 &&
              Math.hypot(rest[0].x - origin.x, rest[0].y - origin.y) >= 0.2;
            if (prependActiveEgo) {
              return [
                { x: origin.x, y: origin.y, z: 0, heading: agent.heading },
                ...rest,
              ];
            }
            if (rest.length === 0) {
              return [{ x: origin.x, y: origin.y, z: 0, heading: agent.heading }];
            }
            return rest.map((w, idx) =>
              idx === 0 && agent.type === 'ego' && isActive
                ? { ...w, heading: w.heading ?? agent.heading }
                : w,
            );
          })();
          const apolloPts =
            this.apolloRoutingPts?.map((p) => ({ x: p.x, y: p.y, z: p.z })) ?? null;
          const useApolloRouting =
            isActive &&
            agent.type === 'ego' &&
            apolloPts != null &&
            isPlausibleRoutePath(apolloPts);
          const alongLaneRaw = useApolloRouting
            ? apolloPts!
            : expandRouteAlongLanes(this.map, pathWaypoints);
          const alongLane = extendPathToTerminal(this.map, alongLaneRaw, {
            x: terminalRender.x,
            y: terminalRender.y,
            z: terminalRender.z ?? 0,
          });
          const displayPts = densifyForDisplay(
            alongLane.map((p) => new THREE.Vector3(p.x, p.y, ROUTE_LINE_Z)),
            isSelected ? 0.28 : 0.4,
          );

          if (displayPts.length >= 2) {
            if (isSelected) {
              const ribbonWidth = estimateSelectedRibbonWidth(this.map, displayPts);
              addRouteRibbon(
                this.routeGroup,
                displayPts,
                routeColor,
                ribbonWidth,
                isActive ? 0.38 : 0.32,
              );
            } else {
              addRouteTrajectoryIdle(this.routeGroup, displayPts, ROUTE_LINE_Z);
            }
          }

          if (isSelected) {
            route.waypoints.forEach((w, idx) => {
              const pinPos = toRenderCoords(w.position, this.map);
              const pin = createNumberedPin(idx, routeColor);
              pin.position.set(pinPos.x, pinPos.y, ROUTE_LINE_Z + 0.25);
              this.routeGroup.add(pin);
            });
            if (route.waypoints.length >= 1) {
              const flag = createDestinationFlag();
              flag.position.set(terminalRender.x, terminalRender.y, 0);
              this.routeGroup.add(flag);
            }
          }
        });
      }
    }

    if (this.transformActive && this.toolMode === 'select') {
      if (this.selectedTriggerId) {
        this.transformingTrigger = true;
        this.attachTriggerTransform(this.selectedTriggerId);
      } else if (this.primaryId) {
        this.transformingTrigger = false;
        this.attachTransform(this.primaryId);
      } else {
        this.transformControls.detach();
      }
    } else if (!this.transformDragging) {
      this.transformControls.detach();
    }
  }

  dispose() {
    cancelAnimationFrame(this.animationId);
    window.removeEventListener('resize', this.handleResize);
    this.controls.dispose();
    this.transformControls.dispose();
    this.renderer.dispose();
    this.boxHelper.remove();
    if (this.renderer.domElement.parentElement === this.container) {
      this.container.removeChild(this.renderer.domElement);
    }
  }
}

export function placeSizeFor(type: AgentType | TriggerType | string): Vec3 {
  return PLACE_SIZE[type] ?? PLACE_SIZE.vehicle;
}
