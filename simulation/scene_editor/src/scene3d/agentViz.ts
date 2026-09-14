import * as THREE from 'three';
import type { AgentType } from '../core/types';

const BODY_NAME = 'agent-body';
const EDGES_NAME = 'agent-edges';
const FOOTPRINT_NAME = 'agent-footprint';
const BOTTOM_TRI_NAME = 'heading-bottom';
const TOP_TRI_NAME = 'heading-top';

function triangleMesh(
  a: THREE.Vector3,
  b: THREE.Vector3,
  c: THREE.Vector3,
  material: THREE.Material,
): THREE.Mesh {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(
      [a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z],
      3,
    ),
  );
  geo.setIndex([0, 1, 2]);
  geo.computeVertexNormals();
  return new THREE.Mesh(geo, material);
}

const headingTriMaterial = () =>
  new THREE.MeshBasicMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.92,
    side: THREE.DoubleSide,
    depthWrite: false,
    toneMapped: false,
  });

/**
 * Agent visual:
 * - 3D mode: translucent box + heading triangles
 * - 2D mode: filled footprint rectangle on the ground plane (no extrusion)
 */
export function createAgentVisual(agentType: AgentType, color: string): THREE.Group {
  const group = new THREE.Group();
  const baseColor = new THREE.Color(color);

  const body = new THREE.Mesh(
    new THREE.BoxGeometry(1, 1, 1),
    new THREE.MeshStandardMaterial({
      color: baseColor,
      transparent: true,
      opacity: 0.36,
      depthWrite: false,
      side: THREE.DoubleSide,
      metalness: 0.05,
      roughness: 0.9,
    }),
  );
  body.name = BODY_NAME;
  body.userData.pickable = true;

  const edgeColor = baseColor.clone().offsetHSL(0, 0, 0.18);
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
    new THREE.LineBasicMaterial({
      color: edgeColor,
      transparent: true,
      opacity: 0.95,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  edges.name = EDGES_NAME;
  edges.userData.pickable = true;

  // Unit square on XY, used as 2D footprint (scaled by agent size.xy)
  const footprint = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({
      color: baseColor,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false,
    }),
  );
  footprint.name = FOOTPRINT_NAME;
  footprint.userData.pickable = true;
  footprint.visible = false;

  group.add(body, edges, footprint);

  if (agentType !== 'static') {
    const triMat = headingTriMaterial();
    const zBot = -0.502;
    const bottomTri = triangleMesh(
      new THREE.Vector3(0.48, 0, zBot),
      new THREE.Vector3(0, -0.38, zBot),
      new THREE.Vector3(0, 0.38, zBot),
      triMat,
    );
    bottomTri.name = BOTTOM_TRI_NAME;

    const zTop = 0.502;
    const topTri = triangleMesh(
      new THREE.Vector3(0.34, 0, zTop),
      new THREE.Vector3(0.08, -0.15, zTop),
      new THREE.Vector3(0.08, 0.15, zTop),
      triMat.clone(),
    );
    topTri.name = TOP_TRI_NAME;

    // 2D heading arrow drawn on the footprint plane
    const flatTri = triangleMesh(
      new THREE.Vector3(0.48, 0, 0.02),
      new THREE.Vector3(0, -0.38, 0.02),
      new THREE.Vector3(0, 0.38, 0.02),
      headingTriMaterial(),
    );
    flatTri.name = 'heading-flat';
    flatTri.visible = false;

    group.add(bottomTri, topTri, flatTri);
  }

  group.userData.viewMode = '3d';
  return group;
}

/** Switch agent rendering between 3D extruded box and 2D footprint. */
export function setAgentViewMode(group: THREE.Group, mode: '3d' | '2d') {
  if (group.userData.viewMode === mode) return;
  group.userData.viewMode = mode;
  const is2d = mode === '2d';

  const body = group.getObjectByName(BODY_NAME);
  const edges = group.getObjectByName(EDGES_NAME);
  const footprint = group.getObjectByName(FOOTPRINT_NAME);
  const topTri = group.getObjectByName(TOP_TRI_NAME);
  const bottomTri = group.getObjectByName(BOTTOM_TRI_NAME);
  const flatTri = group.getObjectByName('heading-flat');

  if (body) body.visible = !is2d;
  if (edges) edges.visible = !is2d;
  if (footprint) footprint.visible = is2d;
  if (topTri) topTri.visible = !is2d;
  if (bottomTri) bottomTri.visible = !is2d;
  if (flatTri) flatTri.visible = is2d;
}

export function updateAgentVisual(
  group: THREE.Group,
  color: string,
  selected: boolean,
  enabled = true,
) {
  const body = group.getObjectByName(BODY_NAME) as THREE.Mesh | undefined;
  const edges = group.getObjectByName(EDGES_NAME) as THREE.LineSegments | undefined;
  const footprint = group.getObjectByName(FOOTPRINT_NAME) as THREE.Mesh | undefined;
  // Disable：灰色，表示未激活
  const baseColor = new THREE.Color(enabled ? color : '#94a3b8');

  if (body) {
    const mat = body.material as THREE.MeshStandardMaterial;
    mat.color.copy(baseColor);
    mat.emissive.set(selected ? 0x1d4ed8 : 0x000000);
    mat.emissiveIntensity = selected ? 0.45 : 0;
    mat.opacity = enabled ? (selected ? 0.46 : 0.36) : selected ? 0.4 : 0.28;
  }
  if (edges) {
    const mat = edges.material as THREE.LineBasicMaterial;
    mat.color.copy(baseColor).offsetHSL(0, 0, enabled ? 0.18 : 0.05);
    mat.opacity = enabled ? 0.95 : 0.55;
  }
  if (footprint) {
    const mat = footprint.material as THREE.MeshBasicMaterial;
    mat.color.copy(baseColor);
    mat.opacity = enabled ? (selected ? 0.95 : 0.85) : selected ? 0.7 : 0.45;
  }
}

export function disposeAgentVisual(root: THREE.Object3D) {
  root.traverse((obj) => {
    if (!(obj instanceof THREE.Mesh) && !(obj instanceof THREE.LineSegments)) return;
    obj.geometry.dispose();
    const { material } = obj;
    if (Array.isArray(material)) material.forEach((m) => m.dispose());
    else material.dispose();
  });
}
