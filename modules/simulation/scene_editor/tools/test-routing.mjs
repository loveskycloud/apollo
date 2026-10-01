import { readFileSync } from 'fs';
import { createRequire } from 'module';

// Quick routing smoke test — run after `npm run build` isn't needed; use vite-node
import { pathToFileURL } from 'url';

async function main() {
  const { parseApolloBaseMapText } = await import('../src/map/loaders/apolloBaseMapText.ts');
  const { expandRouteAlongLanes } = await import('../src/map/routeAlongLanes.ts');
  const { routeBetweenPoints } = await import('../src/core/routing.ts');
  const { nearestLanePoint } = await import('../src/map/types.ts');

  const text = readFileSync(new URL('../../../../data/map_data/1haolou_202608241047qh/base_map.txt', import.meta.url), 'utf8');
  const map = parseApolloBaseMapText(text, 'test');
  const origin = map.meta?.origin ?? { x: 0, y: 0, z: 0 };
  const toRender = (p) => ({ x: p.x - origin.x, y: p.y - origin.y, z: 0 });

  console.log('lanes', map.lanes.length, 'origin', origin);

  const wps = [
    { x: 9998.5477, y: 9000003.4294 },
    { x: 9991.2606, y: 9000003.888 },
    { x: 9965.9863, y: 9000006.4473 },
  ].map(toRender);

  for (let i = 0; i < wps.length - 1; i++) {
    const a = { ...wps[i], heading: Math.PI };
    const b = { ...wps[i + 1], heading: Math.PI };
    const snapA = nearestLanePoint(map, a, Math.PI);
    const snapB = nearestLanePoint(map, b, Math.PI);
    console.log(`seg ${i} snap`, snapA?.laneId, snapA?.distance?.toFixed(2), '->', snapB?.laneId, snapB?.distance?.toFixed(2));
    const r = routeBetweenPoints(map, a, b, { startHeading: Math.PI, endHeading: Math.PI, maxDetourRatio: 6 });
    console.log(`seg ${i}:`, r ? `ok ${r.waypoints.length} pts len=${r.length.toFixed(1)}` : 'FAILED');
  }

  const expanded = expandRouteAlongLanes(
    map,
    wps.map((p) => ({ ...p, heading: Math.PI })),
  );
  console.log('expanded pts', expanded.length);
  for (let i = 1; i < expanded.length; i++) {
    const d = Math.hypot(expanded[i].x - expanded[i - 1].x, expanded[i].y - expanded[i - 1].y);
    if (d > 15) console.log('LONG SEG', i, 'dist', d.toFixed(1));
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
