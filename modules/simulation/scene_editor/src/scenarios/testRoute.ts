import type { Vec3 } from '../core/types';

/** 1haolou 实测测试路线 — 全部为 Apollo ENU，与 SendRouting 一致 */
export const TEST_ROUTE_1HAOLOU = {
  ego: {
    position: { x: 9998.5477, y: 9000003.4294, z: 0.4 } as Vec3,
    heading: Math.PI,
  },
  waypoints: [
    { x: 9998.5477, y: 9000003.4294, z: 0 },
    { x: 9991.2606, y: 9000003.888, z: 0 },
    { x: 9965.9863, y: 9000006.4473, z: 0 },
  ] as Vec3[],
  routeName: 'test route (Lane_1 → Lane_9)',
} as const;
