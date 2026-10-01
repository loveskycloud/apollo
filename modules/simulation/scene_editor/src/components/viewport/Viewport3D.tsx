import { useEffect, useRef, useState } from 'react';
import { message } from 'antd';
import { ScenarioViewport } from '../../scene3d/ScenarioViewport';
import { useScenarioStore } from '../../core/store';
import { stepSimulation } from '../../core/playback';
import { useApolloStore, scheduleRoutePreview } from '../../apollo/apolloStore';
import { useViewStore } from '../../app/viewStore';
import { fromRenderCoords, toRenderCoords } from '../../apollo/coords';
import type { AgentType, TriggerType } from '../../core/types';
import type { RoutingPreviewPoint } from '../../scene3d/routeViz';
import { isAgentActive } from '../../core/agentActive';

/** 与 WorldSim StableObstacleId 一致，用于过滤 prediction 轨迹 */
function stableObstacleId(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const v = h & 0x7fffffff;
  return v === 0 ? 1 : v;
}

export function Viewport3D() {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<ScenarioViewport | null>(null);
  const fittedMapId = useRef<string | null>(null);

  const scenario = useScenarioStore((s) => s.scenario);
  const runtime = useScenarioStore((s) => s.runtime);
  const selected = useScenarioStore((s) => s.selected);
  const selectedIds = useScenarioStore((s) => s.selectedIds);
  const map = useScenarioStore((s) => s.map);
  const mapLayers = useScenarioStore((s) => s.mapLayers);
  const toolMode = useScenarioStore((s) => s.toolMode);
  const placeType = useScenarioStore((s) => s.placeType);
  const transformMode = useScenarioStore((s) => s.transformMode);
  const transformActive = useScenarioStore((s) => s.transformActive);
  const playback = useScenarioStore((s) => s.playback);

  const simRunning = useApolloStore((s) => s.simRunning);
  const apolloStatus = useApolloStore((s) => s.status);
  const trajectory = useApolloStore((s) => s.trajectory);
  const vehicleParam = useApolloStore((s) => s.vehicleParam);
  const predictionTrajectories = useApolloStore((s) => s.predictionTrajectories);
  const routingPoints = useApolloStore((s) => s.routingPoints);
  const routingPath = useApolloStore((s) => s.routingPath);
  const [routingHover, setRoutingHover] = useState<RoutingPreviewPoint | null>(null);

  useEffect(() => {
    if (!hostRef.current) return;
    const viewport = new ScenarioViewport(hostRef.current);
    viewportRef.current = viewport;

    viewport.callbacks = {
      onReady: (backend) => useScenarioStore.getState().setRendererBackend(backend),
      onSelectAgents: (ids, additive) => {
        const store = useScenarioStore.getState();
        if (!ids.length) {
          if (!additive) store.setSelectedIds([]);
          return;
        }
        // 点选绑定：把 Agent 绑到当前 Trigger 的检测对象（任意 Agent，不限主车）
        if (store.pendingBindTriggerId && ids[0]) {
          if (store.consumeBindTriggerTarget(ids[0])) {
            message.success('已绑定检测对象');
          }
          return;
        }
        if (additive) {
          ids.forEach((id) => store.select({ kind: 'agent', id }, true));
        } else {
          store.setSelectedIds(ids);
        }
      },
      onSelectTrigger: (id) => {
        const store = useScenarioStore.getState();
        if (!id) {
          store.select(null);
          return;
        }
        store.select({ kind: 'trigger', id });
      },
      onPlace: (point) => {
        const state = useScenarioStore.getState();
        const t = state.placeType;
        if (!t) return;
        const apollo = fromRenderCoords(point, state.map);
        if (['ego', 'vehicle', 'pedestrian', 'loader', 'static'].includes(t)) {
          const ok = state.addAgent(t as AgentType, {
            x: apollo.x,
            y: apollo.y,
            z: t === 'ego' || t === 'vehicle' ? 0.4 : 1,
          });
          if (!ok) {
            message.warning('场景中已有主车，请先删除再建');
            return;
          }
          message.success(t === 'ego' ? '已放置主车' : `已放置 ${t}`);
          return;
        }
        if (['location', 'agent_distance'].includes(t)) {
          state.addTrigger(t as TriggerType, apollo);
          message.success(t === 'location' ? '已放置区域触发器' : `已放置 ${t} 触发器`);
        }
      },
      onRoutePoint: (point, heading) => {
        const state = useScenarioStore.getState();
        const apollo = fromRenderCoords(point, state.map);
        const sel = state.selected;
        const agentId =
          sel?.kind === 'agent'
            ? sel.id
            : sel?.kind === 'route'
              ? sel.agentId
              : state.selectedIds[0] ?? 'ego';
        const agent = state.scenario.agents.find((a) => a.id === agentId);
        if (!agent) return;
        // 路点追加到当前编辑中的 Route（selected），不是仿真初始 activeRouteId
        let routeId =
          sel?.kind === 'route' && sel.agentId === agent.id
            ? sel.routeId
            : agent.activeRouteId;
        if (!routeId) {
          state.addRoute(agent.id);
          const next = useScenarioStore.getState();
          const nextSel = next.selected;
          routeId =
            nextSel?.kind === 'route' && nextSel.agentId === agent.id
              ? nextSel.routeId
              : next.scenario.agents.find((a) => a.id === agent.id)?.activeRouteId;
        }
        // 落点保持点击坐标（行人自由平面 / 车辆已在视口校验车道内）
        if (routeId) state.addWaypoint(agent.id, routeId, apollo, heading);
      },
      onBezierHandleMoved: (agentId, routeId, waypointId, which, position) => {
        const state = useScenarioStore.getState();
        const apollo = fromRenderCoords(position, state.map);
        state.updateWaypointHandle(agentId, routeId, waypointId, which, apollo);
      },
      onRoutingPoint: (point, heading) => {
        const state = useScenarioStore.getState();
        const apolloPt = fromRenderCoords(point, state.map);
        const apolloStore = useApolloStore.getState();
        if (apolloStore.status === 'connected') {
          const count = apolloStore.routingPoints.length;
          apolloStore.pushRoutingPoint(apolloPt, heading);
          if (count === 0) {
            message.info('已选起点，请再点终点');
          } else {
            message.success('路由已下发');
            setRoutingHover(null);
          }
          return;
        }
        const selected = state.selected;
        const agent =
          selected?.kind === 'agent'
            ? state.scenario.agents.find((a) => a.id === selected.id)
            : selected?.kind === 'route'
              ? state.scenario.agents.find((a) => a.id === selected.agentId)
              : state.scenario.agents.find((a) => a.type === 'ego');
        if (!agent) return;
        if (!state.routingStart) {
          state.setRoutingStart(apolloPt);
          message.info('已选起点，请再点终点');
          return;
        }
        state.applyRouting(agent.id, state.routingStart, apolloPt);
        message.success('路径已沿车道生成');
      },
      onInvalidLanePick: () => {
        message.warning('请将点设置在车道内');
      },
      onHoverPoint: (point) => {
        if (useScenarioStore.getState().toolMode !== 'routing') {
          setRoutingHover(null);
          return;
        }
        if (!point) {
          setRoutingHover(null);
          return;
        }
        setRoutingHover({
          x: point.x,
          y: point.y,
          heading: point.heading,
        });
      },
      onAgentsTransformed: (ids, delta) => {
        const store = useScenarioStore.getState();
        store.pushHistory();
        if (delta.positions) {
          store.setAgentsPositions(delta.positions, { moveRoutes: true });
        } else if (delta.position) {
          store.moveAgents(ids, delta.position, { moveRoutes: true });
        }
        if (delta.heading != null) store.rotateAgents(ids, delta.heading);
        if (delta.sizes) store.setAgentsSize(delta.sizes);
        else if (delta.scale != null) store.scaleAgents(ids, delta.scale);
      },
      onTriggerTransformed: (id, patch) => {
        const store = useScenarioStore.getState();
        store.updateTrigger(id, patch);
      },
    };

    void useScenarioStore.getState().init();
    return () => viewport.dispose();
  }, []);

  useEffect(() => {
    viewportRef.current?.setMap(map, mapLayers);
    if (map && fittedMapId.current !== map.id) {
      viewportRef.current?.fitToMap(map);
      fittedMapId.current = map.id;
    }
  }, [map, mapLayers]);

  useEffect(() => {
    viewportRef.current?.setInteractionState({
      toolMode,
      placeType,
      transformMode,
      transformActive,
      selectedIds,
      selected,
    });
  }, [toolMode, placeType, transformMode, transformActive, selectedIds, selected]);

  useEffect(() => {
    if (toolMode !== 'routing') {
      setRoutingHover(null);
    }
  }, [toolMode]);

  useEffect(() => {
    const selectedTriggerId = selected?.kind === 'trigger' ? selected.id : null;
    const editingRouteId = selected?.kind === 'route' ? selected.routeId : null;
    viewportRef.current?.syncScenario(
      scenario,
      runtime,
      selectedIds,
      selectedTriggerId,
      simRunning,
      editingRouteId,
    );
  }, [scenario, runtime, selectedIds, selected, simRunning]);

  // Apollo PnC：仅仿真运行中绘制 planning / prediction 轨迹
  useEffect(() => {
    if (!simRunning) {
      viewportRef.current?.setPlanningTrajectory(null);
      viewportRef.current?.setPredictionTrajectories(null);
      return;
    }
    const ego = scenario.agents.find((a) => a.type === 'ego');
    const vp = vehicleParam as { width?: number; vehicle_width?: number } | null;
    const width = vp?.width ?? vp?.vehicle_width ?? ego?.size?.y ?? 1.0;
    viewportRef.current?.setPlanningTrajectory(
      trajectory.length >= 2 ? trajectory : null,
      Math.max(0.5, Number(width) || 1.0),
    );
  }, [simRunning, trajectory, vehicleParam, scenario.agents]);
  useEffect(() => {
    if (!simRunning) {
      viewportRef.current?.setPredictionTrajectories(null);
      return;
    }
    // Disable Agent 不进 gt_obstacles；前端再按 stable id 滤一层
    const activeObsIds = new Set<string>();
    for (const a of scenario.agents) {
      if (isAgentActive(a, runtime[a.id])) {
        activeObsIds.add(String(stableObstacleId(a.id)));
        activeObsIds.add(a.id);
      }
    }
    const filtered = predictionTrajectories.filter((t) => {
      const raw = String(t.id).split('_')[0];
      return activeObsIds.has(raw) || activeObsIds.has(String(t.id));
    });
    viewportRef.current?.setPredictionTrajectories(
      filtered.length > 0 ? filtered : null,
    );
  }, [simRunning, predictionTrajectories, scenario.agents, runtime]);
  useEffect(() => {
    const map = useScenarioStore.getState().map;
    const placed: RoutingPreviewPoint[] = routingPoints.map((p) => {
      const r = toRenderCoords(p, map);
      return { x: r.x, y: r.y, heading: p.heading };
    });
    viewportRef.current?.setRoutingPreview(placed, routingHover);
  }, [routingPoints, routingHover]);
  useEffect(() => {
    if (routingPath.length < 2) {
      viewportRef.current?.setApolloRoutingPath(null);
      return;
    }
    const m = useScenarioStore.getState().map;
    const pts = routingPath.map((p) => toRenderCoords(p, m));
    viewportRef.current?.setApolloRoutingPath(pts);
    // 触发 route 重绘
    const s = useScenarioStore.getState();
    viewportRef.current?.syncScenario(
      s.scenario,
      s.runtime,
      s.selectedIds,
      s.selected?.kind === 'trigger' ? s.selected.id : null,
      simRunning,
      s.selected?.kind === 'route' ? s.selected.routeId : null,
    );
  }, [routingPath, map, simRunning]);

  // 非仿真：路点/主车变更时向 Apollo 请求沿车道路由预览
  useEffect(() => {
    if (apolloStatus !== 'connected' || simRunning) return;
    scheduleRoutePreview();
  }, [apolloStatus, simRunning, scenario, map?.id]);

  // 视图菜单：相机模式 / 适应地图
  const cameraMode = useViewStore((s) => s.cameraMode);
  const fitTick = useViewStore((s) => s.fitTick);
  useEffect(() => {
    // chase/driver 尚未实现，暂与 free 共用透视轨道相机
    const viewportMode = cameraMode === 'top' ? 'top' : 'free';
    viewportRef.current?.setCameraMode(viewportMode);
  }, [cameraMode]);
  useEffect(() => {
    if (fitTick === 0) return;
    const map = useScenarioStore.getState().map;
    if (map) viewportRef.current?.fitToMap(map);
  }, [fitTick]);

  useEffect(() => {
    if (!playback.playing) return;
    let frame = 0;
    let last = performance.now();
    const loop = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const store = useScenarioStore.getState();
      if (!store.playback.playing) return;
      const stepped = stepSimulation(
        store.scenario,
        store.runtime,
        store.playback.time,
        dt * store.playback.speed,
      );
      for (const id of stepped.firedTriggerIds) store.markTriggerFired(id);
      useScenarioStore.setState({ runtime: stepped.runtime });
      store.tickPlayback(dt);
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [playback.playing]);

  return (
    <div className="viewport-wrap">
      <div className="viewport-hud">
        <span>
          {toolMode === 'place'
            ? `放置预览: ${placeType ?? ''}`
              : transformActive
              ? `变换中: ${transformMode.toUpperCase()}（Esc/Q 退出）`
              : toolMode === 'routing' || toolMode === 'route_edit'
                ? '车道内选点（绿点可点 / 红点出界）'
                : '选择模式（E移动 / R旋转 / S缩放）'}
        </span>
        <span>{map?.name ?? 'No Map'}</span>
        <span>选中 {selectedIds.length}</span>
      </div>
      <div ref={hostRef} className="viewport-canvas" />
    </div>
  );
}
