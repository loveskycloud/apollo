/**
 * 验收：默认测试路线在 1haolou 地图上应沿车道展开，不得出现 >15m 的穿图直线段。
 * 运行：npx tsx tools/verify-routing.mjs
 */
import { readFileSync } from 'fs';
import { parseApolloBaseMapText } from '../src/map/loaders/apolloBaseMapText.ts';
import { expandRouteAlongLanes, extendPathToTerminal, isPlausibleRoutePath } from '../src/map/routeAlongLanes.ts';
import { TEST_ROUTE_1HAOLOU } from '../src/scenarios/testRoute.ts';

const text = readFileSync('public/maps/1haolou_202608241047qh/base_map.txt', 'utf8');
const map = parseApolloBaseMapText(text, 'verify');
const origin = map.meta?.origin ?? { x: 0, y: 0, z: 0 };
const toRender = (p) => ({
  x: p.x - origin.x,
  y: p.y - origin.y,
  z: 0,
  heading: TEST_ROUTE_1HAOLOU.ego.heading,
});

const wps = TEST_ROUTE_1HAOLOU.waypoints.map(toRender);
const expanded = expandRouteAlongLanes(map, wps);

let failed = false;
if (expanded.length < 50) {
  console.error('FAIL: too few points', expanded.length);
  failed = true;
}
if (!isPlausibleRoutePath(expanded)) {
  console.error('FAIL: path not plausible (looks like shortcut)');
  failed = true;
}
for (let i = 1; i < expanded.length; i += 1) {
  const d = Math.hypot(expanded[i].x - expanded[i - 1].x, expanded[i].y - expanded[i - 1].y);
  if (d > 15) {
    console.error('FAIL: long straight segment at', i, 'dist', d.toFixed(1));
    failed = true;
  }
}
const terminal = wps[wps.length - 1];
const endGap = Math.hypot(
  expanded[expanded.length - 1].x - terminal.x,
  expanded[expanded.length - 1].y - terminal.y,
);
if (endGap > 0.15) {
  console.error('FAIL: path end not aligned with terminal waypoint, gap', endGap.toFixed(3));
  failed = true;
}

if (failed) {
  process.exit(1);
}
console.log('PASS: routing along lanes', expanded.length, 'points');
