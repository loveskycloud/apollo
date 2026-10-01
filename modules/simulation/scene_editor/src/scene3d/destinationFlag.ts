import * as THREE from 'three';

/** 整体（杆+旗）相对摄像机的俯仰倾斜 */
const FLAG_TILT_RAD = THREE.MathUtils.degToRad(-28);

let checkerTex: THREE.CanvasTexture | null = null;

function getCheckerTexture(): THREE.CanvasTexture {
  if (checkerTex) return checkerTex;
  const canvas = document.createElement('canvas');
  canvas.width = 24;
  canvas.height = 16;
  const g = canvas.getContext('2d');
  if (g) {
    for (let r = 0; r < 2; r += 1) {
      for (let c = 0; c < 3; c += 1) {
        g.fillStyle = (r + c) % 2 === 0 ? '#111318' : '#f4f4f5';
        g.fillRect(c * 8, r * 8, 8, 8);
      }
    }
  }
  checkerTex = new THREE.CanvasTexture(canvas);
  checkerTex.magFilter = THREE.NearestFilter;
  checkerTex.colorSpace = THREE.SRGBColorSpace;
  return checkerTex;
}

/**
 * Routing 终点小旗（挂在 Route 末路点）。
 * 杆与旗面是同一刚体：整组永远面对摄像机，并一体倾斜。
 * 地面定位环留在世界 XY，不参与 billboard。
 */
export function createDestinationFlag(): THREE.Group {
  const root = new THREE.Group();
  root.userData.destinationFlag = true;

  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.45, 0.68, 28),
    new THREE.MeshBasicMaterial({
      color: 0x2dd4bf,
      transparent: true,
      opacity: 0.85,
      side: THREE.DoubleSide,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  ring.position.set(0, 0, 0.03);
  root.add(ring);

  // 本地约定（与摄像机默认朝向一致）：+Y 为杆向上，旗面在 XY、朝 +Z
  const rig = new THREE.Group();
  rig.userData.flagBillboardRig = true;

  const pole = new THREE.Mesh(
    new THREE.CylinderGeometry(0.04, 0.05, 1.55, 8),
    new THREE.MeshBasicMaterial({ color: 0xe5e7eb }),
  );
  pole.position.set(0, 0.78, 0);
  rig.add(pole);

  const flag = new THREE.Mesh(
    new THREE.PlaneGeometry(0.95, 0.62),
    new THREE.MeshBasicMaterial({
      map: getCheckerTexture(),
      side: THREE.DoubleSide,
      toneMapped: false,
      depthTest: true,
    }),
  );
  flag.position.set(0.48, 1.28, 0);
  rig.add(flag);

  root.add(rig);
  return root;
}

/** 杆+旗一体：复制摄像机朝向后再整体倾斜 */
export function tickDestinationFlag(root: THREE.Object3D, camera: THREE.Camera) {
  root.traverse((obj) => {
    if (!obj.userData?.flagBillboardRig) return;
    obj.quaternion.copy(camera.quaternion);
    obj.rotateX(FLAG_TILT_RAD);
  });
}
