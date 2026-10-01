import type { HdMap, Vec3 } from '../../core/types';

function lane(
  id: string,
  name: string,
  points: Array<[number, number, number]>,
  width: number,
  successors: string[] = [],
  predecessors: string[] = [],
) {
  const centerline: Vec3[] = points.map(([x, y, z]) => ({ x, y, z }));
  const leftBoundary: Vec3[] = [];
  const rightBoundary: Vec3[] = [];
  for (let i = 0; i < centerline.length; i += 1) {
    const p = centerline[i];
    const prev = centerline[Math.max(0, i - 1)];
    const next = centerline[Math.min(centerline.length - 1, i + 1)];
    const dx = next.x - prev.x;
    const dy = next.y - prev.y;
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len;
    const ny = dx / len;
    const half = width / 2;
    leftBoundary.push({ x: p.x + nx * half, y: p.y + ny * half, z: p.z });
    rightBoundary.push({ x: p.x - nx * half, y: p.y - ny * half, z: p.z });
  }
  return {
    id,
    name,
    centerline,
    leftBoundary,
    rightBoundary,
    width,
    successors,
    predecessors,
  };
}

/** Mock underground mine tunnel HD map (pluggable format). */
export const mineTunnelMap: HdMap = {
  id: 'mine_tunnel_t204',
  name: 'T-204 主运输巷道',
  format: 'mine_lane_json' as const,
  bounds: {
    min: { x: -20, y: -40, z: 0 },
    max: { x: 220, y: 80, z: 0 },
  },
  lanes: [
    lane(
      'lane_main_fwd',
      '主巷前进',
      [
        [0, 0, 0],
        [40, 0, 0],
        [80, 4, 0],
        [120, 8, 0],
        [160, 8, 0],
        [200, 4, 0],
      ],
      6,
      ['lane_branch_a', 'lane_main_end'],
      [],
    ),
    lane(
      'lane_main_end',
      '主巷末端',
      [
        [200, 4, 0],
        [220, 0, 0],
      ],
      6,
      [],
      ['lane_main_fwd'],
    ),
    lane(
      'lane_branch_a',
      '支巷A',
      [
        [120, 8, 0],
        [130, 28, 0],
        [145, 48, 0],
        [160, 60, 0],
      ],
      5,
      [],
      ['lane_main_fwd'],
    ),
    lane(
      'lane_pass_bay',
      '会车硐室',
      [
        [80, 4, 0],
        [85, -12, 0],
        [100, -16, 0],
        [115, -8, 0],
        [120, 8, 0],
      ],
      7,
      ['lane_main_fwd'],
      ['lane_main_fwd'],
    ),
  ],
  nodes: [
    {
      id: 'node_start',
      name: '装载点入口',
      position: { x: 0, y: 0, z: 0 },
      kind: 'load',
      connectedLanes: ['lane_main_fwd'],
    },
    {
      id: 'node_junction',
      name: '岔路口',
      position: { x: 120, y: 8, z: 0 },
      kind: 'junction',
      connectedLanes: ['lane_main_fwd', 'lane_branch_a', 'lane_pass_bay'],
    },
    {
      id: 'node_pass',
      name: '会车点',
      position: { x: 100, y: -16, z: 0 },
      kind: 'pass',
      connectedLanes: ['lane_pass_bay'],
    },
    {
      id: 'node_unload',
      name: '卸载点',
      position: { x: 220, y: 0, z: 0 },
      kind: 'unload',
      connectedLanes: ['lane_main_end'],
    },
    {
      id: 'node_branch_end',
      name: '支巷尽头',
      position: { x: 160, y: 60, z: 0 },
      kind: 'unload',
      connectedLanes: ['lane_branch_a'],
    },
  ],
};

export function loadMineLaneJson(_url?: string): Promise<HdMap> {
  return Promise.resolve(mineTunnelMap);
}
