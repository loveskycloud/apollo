import { useScenarioStore } from '../core/store';
import type { HdMap, Vec3 } from '../core/types';

/** 场景 Agent / Route 存 Apollo ENU；地图渲染用 origin 归一化坐标。 */
export function mapOrigin(map: HdMap | null = useScenarioStore.getState().map): Vec3 {
  return map?.meta?.origin ?? { x: 0, y: 0, z: 0 };
}

/** Apollo 地图下场景坐标系为 ENU */
export function usesApolloCoords(map: HdMap | null = useScenarioStore.getState().map): boolean {
  return map?.format === 'apollo_base_map' && map.meta?.origin != null;
}

/** @deprecated 使用 usesApolloCoords */
export function hasApolloOrigin(map: HdMap | null = useScenarioStore.getState().map): boolean {
  return usesApolloCoords(map);
}

/** 场景 Apollo ENU → 地图/视口渲染坐标 */
export function toRenderCoords(
  p: { x: number; y: number; z?: number },
  map: HdMap | null = useScenarioStore.getState().map,
): Vec3 {
  if (!usesApolloCoords(map)) return { x: p.x, y: p.y, z: p.z ?? 0 };
  const o = mapOrigin(map);
  return { x: p.x - o.x, y: p.y - o.y, z: (p.z ?? 0) - (o.z ?? 0) };
}

/** 视口/地图交互坐标 → 场景 Apollo ENU */
export function fromRenderCoords(
  p: { x: number; y: number; z?: number },
  map: HdMap | null = useScenarioStore.getState().map,
): Vec3 {
  if (!usesApolloCoords(map)) return { x: p.x, y: p.y, z: p.z ?? 0 };
  const o = mapOrigin(map);
  return { x: p.x + o.x, y: p.y + o.y, z: (p.z ?? 0) + (o.z ?? 0) };
}

/** @deprecated 场景已为 Apollo ENU，请直接使用 position；仅视口边界需要 toRenderCoords */
export function toApollo(
  p: { x: number; y: number; z?: number },
  map: HdMap | null = useScenarioStore.getState().map,
): Vec3 {
  if (usesApolloCoords(map)) return { x: p.x, y: p.y, z: p.z ?? 0 };
  const o = mapOrigin(map);
  return { x: p.x + o.x, y: p.y + o.y, z: (p.z ?? 0) + (o.z ?? 0) };
}

/** @deprecated 请使用 toRenderCoords */
export function toEditor(
  p: { x: number; y: number; z?: number },
  map: HdMap | null = useScenarioStore.getState().map,
): Vec3 {
  return toRenderCoords(p, map);
}
