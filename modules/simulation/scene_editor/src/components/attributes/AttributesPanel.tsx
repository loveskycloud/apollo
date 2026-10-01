import { Button, Input, InputNumber, Select, Switch, message } from 'antd';
import { usesApolloCoords } from '../../apollo/coords';
import { useScenarioStore } from '../../core/store';
import type { BehaviorAction } from '../../core/types';
import { HeadingDial } from './HeadingDial';

export function AttributesPanel() {
  const scenario = useScenarioStore((s) => s.scenario);
  const map = useScenarioStore((s) => s.map);
  const apolloCoords = usesApolloCoords(map);
  const selected = useScenarioStore((s) => s.selected);
  const updateAgent = useScenarioStore((s) => s.updateAgent);
  const updateTrigger = useScenarioStore((s) => s.updateTrigger);
  const armBindTriggerTarget = useScenarioStore((s) => s.armBindTriggerTarget);
  const pendingBindTriggerId = useScenarioStore((s) => s.pendingBindTriggerId);
  const alignAgentToLane = useScenarioStore((s) => s.alignAgentToLane);
  const addRoute = useScenarioStore((s) => s.addRoute);
  const deleteRoute = useScenarioStore((s) => s.deleteRoute);
  const setActiveRoute = useScenarioStore((s) => s.setActiveRoute);
  const updateWaypoint = useScenarioStore((s) => s.updateWaypoint);
  const insertWaypoint = useScenarioStore((s) => s.insertWaypoint);
  const deleteWaypoint = useScenarioStore((s) => s.deleteWaypoint);
  const showRoute = useScenarioStore((s) => s.showRoute);
  const snapRouteToCenterline = useScenarioStore((s) => s.snapRouteToCenterline);
  const select = useScenarioStore((s) => s.select);
  const setToolMode = useScenarioStore((s) => s.setToolMode);

  if (!selected) {
    return (
      <div className="attr-block" style={{ marginTop: 12 }}>
        <h4>对象属性</h4>
        <div style={{ color: '#8b93a1', fontSize: 12 }}>未选中对象</div>
      </div>
    );
  }

  if (selected.kind === 'agent' || selected.kind === 'route') {
    const agentId = selected.kind === 'agent' ? selected.id : selected.agentId;
    const agent = scenario.agents.find((a) => a.id === agentId);
    if (!agent) return null;
    // 编辑选中 ≠ 仿真初始：activeRouteId 按添加顺序默认第一条；列表高亮/删改针对 editingRouteId
    const editingRouteId =
      selected.kind === 'route' && selected.agentId === agent.id
        ? selected.routeId
        : agent.activeRouteId ?? agent.routes[0]?.id;

    return (
      <div style={{ paddingTop: 8 }}>
        <div className="attr-block">
          <h4>标识</h4>
          <div className="field-row">
            <label>类型</label>
            <Select
              size="small"
              value={agent.type}
              disabled={agent.type === 'ego'}
              options={[
                { value: 'ego', label: 'Ego Mine Truck' },
                { value: 'vehicle', label: 'Vehicle' },
                { value: 'loader', label: 'Loader' },
                { value: 'pedestrian', label: 'Pedestrian' },
                { value: 'static', label: 'Static' },
              ]}
              onChange={(type) => updateAgent(agent.id, { type })}
            />
          </div>
          <div className="field-row">
            <label>ID</label>
            <Input size="small" value={agent.id} disabled />
          </div>
          <div className="field-row">
            <label>名称</label>
            <Input
              size="small"
              value={agent.name}
              disabled={agent.type === 'ego'}
              onChange={(e) => updateAgent(agent.id, { name: e.target.value })}
            />
          </div>
          {agent.type !== 'ego' && (
            <div className="field-row">
              <label>启用 Enabled</label>
              <Switch
                size="small"
                checked={agent.enabled !== false}
                onChange={(checked) => updateAgent(agent.id, { enabled: checked })}
              />
            </div>
          )}
        </div>

        <div className="attr-block">
          <h4>位姿与运动</h4>
          <div className="field-row">
            <label>{apolloCoords ? '坐标 (ENU)' : '坐标'}</label>
            <div className="field-inline">
              <InputNumber
                size="small"
                value={Number(agent.position.x.toFixed(3))}
                onChange={(v) =>
                  updateAgent(agent.id, { position: { ...agent.position, x: Number(v ?? 0) } })
                }
              />
              <InputNumber
                size="small"
                value={Number(agent.position.y.toFixed(3))}
                onChange={(v) =>
                  updateAgent(agent.id, { position: { ...agent.position, y: Number(v ?? 0) } })
                }
              />
              <InputNumber
                size="small"
                value={Number(agent.position.z.toFixed(3))}
                onChange={(v) =>
                  updateAgent(agent.id, { position: { ...agent.position, z: Number(v ?? 0) } })
                }
              />
            </div>
          </div>
          <div className="field-row">
            <label>长度 L</label>
            <InputNumber
              size="small"
              style={{ width: '100%' }}
              min={0.01}
              step={0.01}
              value={Number(agent.size.x.toFixed(3))}
              addonAfter="m"
              onChange={(v) => {
                const x = Math.max(0.01, Number(v ?? 0.01));
                updateAgent(agent.id, { size: { ...agent.size, x } });
              }}
            />
          </div>
          <div className="field-row">
            <label>宽度 W</label>
            <InputNumber
              size="small"
              style={{ width: '100%' }}
              min={0.01}
              step={0.01}
              value={Number(agent.size.y.toFixed(3))}
              addonAfter="m"
              onChange={(v) => {
                const y = Math.max(0.01, Number(v ?? 0.01));
                updateAgent(agent.id, { size: { ...agent.size, y } });
              }}
            />
          </div>
          <div className="field-row">
            <label>高度 H</label>
            <InputNumber
              size="small"
              style={{ width: '100%' }}
              min={0.01}
              step={0.01}
              value={Number(agent.size.z.toFixed(3))}
              addonAfter="m"
              onChange={(v) => {
                const z = Math.max(0.01, Number(v ?? 0.01));
                updateAgent(agent.id, {
                  size: { ...agent.size, z },
                  position: { ...agent.position, z: z / 2 },
                });
              }}
            />
          </div>
          <div className="field-row heading-row">
            <label>航向</label>
            <div className="heading-controls">
              <HeadingDial
                value={agent.heading}
                onChange={(rad) => updateAgent(agent.id, { heading: rad })}
              />
              <div className="heading-side">
                <InputNumber
                  size="small"
                  style={{ width: '100%' }}
                  value={Number(((agent.heading * 180) / Math.PI).toFixed(1))}
                  addonAfter="°"
                  step={1}
                  onChange={(v) =>
                    updateAgent(agent.id, { heading: (Number(v ?? 0) * Math.PI) / 180 })
                  }
                />
                <Button
                  size="small"
                  block
                  onClick={() => {
                    const ok = alignAgentToLane(agent.id);
                    if (ok) message.success('已对齐最近车道方向');
                    else message.warning('附近未找到车道');
                  }}
                >
                  对齐车道
                </Button>
              </div>
            </div>
          </div>
          <div className="field-row">
            <label>速度</label>
            <InputNumber
              size="small"
              style={{ width: '100%' }}
              value={agent.speed}
              addonAfter="m/s"
              min={0}
              step={0.1}
              onChange={(v) => updateAgent(agent.id, { speed: Number(v ?? 0) })}
            />
          </div>
        </div>

        <div className="attr-block route-attr-block">
          <div className="route-panel">
            <div className="routes-header">
              <span className="routes-title">Routes ({agent.routes.length})</span>
              <div className="routes-header-actions">
                <Button size="small" onClick={() => addRoute(agent.id)}>
                  Add route
                </Button>
                <Button
                  size="small"
                  danger
                  disabled={!editingRouteId}
                  onClick={() => {
                    if (!editingRouteId) return;
                    deleteRoute(agent.id, editingRouteId);
                    message.success('已删除 Route');
                  }}
                >
                  Delete route
                </Button>
              </div>
            </div>

            <div className="route-list">
              {agent.routes.map((r) => {
                const isEditing = r.id === editingRouteId;
                const isInitial = r.id === agent.activeRouteId;
                return (
                  <button
                    key={r.id}
                    type="button"
                    className={[
                      'route-list-item',
                      isEditing ? 'active' : '',
                      isInitial ? 'initial' : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    onClick={() => setActiveRoute(agent.id, r.id)}
                    title={
                      isInitial
                        ? '仿真初始路径（activeRouteId）'
                        : '点击选中以编辑'
                    }
                  >
                    {r.name}
                    {isInitial ? ' · 初始' : ''}
                  </button>
                );
              })}
              {agent.routes.length === 0 && (
                <div className="route-list-empty">尚无 Route，点击 Add route</div>
              )}
            </div>

            {(() => {
              const route =
                agent.routes.find((r) => r.id === editingRouteId) ?? agent.routes[0];
              if (!route) return null;
              return (
                <>
                  <div className="waypoint-table">
                    {apolloCoords && (
                      <div className="route-list-empty" style={{ marginBottom: 6 }}>
                        路点与 Agent 坐标均为 Apollo ENU（与 SendRouting 一致）。
                      </div>
                    )}
                    <div className="waypoint-table-head">
                      <span>#</span>
                      <span>{apolloCoords ? 'X (ENU)' : 'X'}</span>
                      <span>{apolloCoords ? 'Y (ENU)' : 'Y'}</span>
                      <span />
                      <span />
                    </div>
                    {route.waypoints.map((w, idx) => (
                      <div key={w.id} className="waypoint-row">
                        <span className="waypoint-idx">{idx}</span>
                        <InputNumber
                          size="small"
                          value={Number(w.position.x.toFixed(apolloCoords ? 3 : 2))}
                          onChange={(v) => {
                            updateWaypoint(agent.id, route.id, w.id, {
                              ...w.position,
                              x: Number(v ?? 0),
                            });
                          }}
                        />
                        <InputNumber
                          size="small"
                          value={Number(w.position.y.toFixed(apolloCoords ? 3 : 2))}
                          onChange={(v) => {
                            updateWaypoint(agent.id, route.id, w.id, {
                              ...w.position,
                              y: Number(v ?? 0),
                            });
                          }}
                        />
                        <Button
                          size="small"
                          onClick={() => insertWaypoint(agent.id, route.id, idx)}
                        >
                          Insert
                        </Button>
                        <Button
                          size="small"
                          danger
                          onClick={() => deleteWaypoint(agent.id, route.id, w.id)}
                        >
                          Delete
                        </Button>
                      </div>
                    ))}
                    {route.waypoints.length === 0 && (
                    <div className="route-list-empty">
                      {agent.type === 'pedestrian'
                        ? '无路点 — Append 后在任意位置按下并拖动定朝向（贝塞尔）'
                        : '无路点 — Append 后在车道内按下并拖动定朝向'}
                    </div>
                  )}
                </div>
                <div className="route-panel-actions">
                  <Button
                    size="small"
                    type="primary"
                    block
                    onClick={() => {
                      setActiveRoute(agent.id, route.id);
                      select({ kind: 'route', agentId: agent.id, routeId: route.id });
                      setToolMode('route_edit');
                      message.info(
                        agent.type === 'pedestrian'
                          ? '任意位置按下选点，拖动调整朝向；选中后可拖粉色控制柄调曲线'
                          : '在车道内按下选点，拖动调整朝向后松开',
                      );
                    }}
                  >
                    Append
                  </Button>
                  <Button
                    size="small"
                    block
                    onClick={() => {
                      const len = showRoute(agent.id, route.id);
                      if (len == null) {
                        message.warning(
                          agent.type === 'pedestrian'
                            ? '无法展开贝塞尔路径（请至少 2 个路点）'
                            : '无法沿车道展开（请至少 1 个路点且地图已加载）',
                        );
                      } else {
                        message.success(`已显示 Route，约 ${len.toFixed(1)} m`);
                      }
                    }}
                  >
                    Show route
                  </Button>
                  {agent.type !== 'pedestrian' && (
                    <Button
                      size="small"
                      block
                      disabled={route.waypoints.length === 0}
                      onClick={() => {
                        const n = snapRouteToCenterline(agent.id, route.id);
                        if (n == null) {
                          message.warning('无法吸附（需已加载地图且路点附近有车道）');
                        } else {
                          message.success(`已吸附 ${n} 个路点到车道中心线`);
                        }
                      }}
                    >
                      Keep line center
                    </Button>
                  )}
                  </div>
                </>
              );
            })()}
          </div>
        </div>

        <div className="attr-block">
          <h4>路径与行为</h4>
          <div className="field-row">
            <label>Behavior</label>
            <div className="field-actions">
              <Select
                size="small"
                defaultValue="BT_MineTruck_Default"
                options={[
                  { value: 'BT_MineTruck_Default', label: 'BT_MineTruck_Default' },
                  { value: 'BT_Yield', label: 'BT_Yield' },
                  { value: 'BT_LoadUnload', label: 'BT_LoadUnload' },
                ]}
              />
              <Button size="small">编辑</Button>
            </div>
          </div>
        </div>

        <div className="attr-block">
          <h4>触发与环境</h4>
          <div className="field-row">
            <label>摩擦 μ</label>
            <InputNumber size="small" style={{ width: '100%' }} defaultValue={0.42} step={0.01} />
          </div>
          <div className="field-row">
            <label>粉尘</label>
            <InputNumber
              size="small"
              style={{ width: '100%' }}
              defaultValue={1.25}
              step={0.05}
              addonAfter="g/m³"
            />
          </div>
          <div className="field-row">
            <label>丢包</label>
            <InputNumber
              size="small"
              style={{ width: '100%' }}
              defaultValue={0}
              min={0}
              max={100}
              addonAfter="%"
            />
          </div>
        </div>

        <div className="attr-block">
          <h4>
            参数扫描 Var{' '}
            <Switch size="small" style={{ marginLeft: 8 }} defaultChecked={false} />
          </h4>
          <div className="field-row">
            <label>变量</label>
            <Input size="small" defaultValue="agent_speed" />
          </div>
          <div className="field-row">
            <label>范围</label>
            <div className="field-inline">
              <InputNumber size="small" defaultValue={1} />
              <InputNumber size="small" defaultValue={5} />
              <InputNumber size="small" defaultValue={1} />
            </div>
          </div>
        </div>
      </div>
    );
  }

  const trigger = scenario.triggers.find((t) => t.id === selected.id);
  if (!trigger) return null;

  const agentOptions = scenario.agents.map((a) => ({
    value: a.id,
    label: `${a.name} (${a.type})`,
  }));

  const patchActions = (actions: BehaviorAction[]) => {
    updateTrigger(trigger.id, { actions });
  };

  const updateActionAt = (idx: number, patch: Partial<BehaviorAction> & { kind?: BehaviorAction['kind'] }) => {
    const next = trigger.actions.map((a, i) => {
      if (i !== idx) return a;
      const kind = patch.kind ?? a.kind;
      const targetAgentId =
        ('targetAgentId' in patch && patch.targetAgentId) || a.targetAgentId;
      if (kind === 'set_speed') {
        return {
          kind: 'set_speed' as const,
          targetAgentId,
          speed:
            'speed' in patch && patch.speed != null
              ? Number(patch.speed)
              : a.kind === 'set_speed'
                ? a.speed
                : 1.2,
        };
      }
      if (kind === 'stop' || kind === 'enable' || kind === 'disable') {
        return { kind, targetAgentId };
      }
      if (kind === 'start_route' || kind === 'switch_route') {
        const target = scenario.agents.find((x) => x.id === targetAgentId);
        const routeId =
          ('routeId' in patch && patch.routeId) ||
          (a.kind === 'start_route' || a.kind === 'switch_route'
            ? a.routeId
            : target?.activeRouteId ?? target?.routes[0]?.id ?? '');
        return { kind, targetAgentId, routeId };
      }
      return a;
    });
    patchActions(next);
  };

  return (
    <div style={{ paddingTop: 8 }}>
      <div className="attr-block">
        <h4>触发器属性</h4>
        <div className="route-list-empty" style={{ marginBottom: 8 }}>
          条件满足时执行 Actions。区域：选中后用工具栏移动/旋转/缩放，或拖黄色角点改尺寸。
        </div>
        <div className="field-row">
          <label>名称</label>
          <Input
            size="small"
            value={trigger.name}
            onChange={(e) => updateTrigger(trigger.id, { name: e.target.value })}
          />
        </div>
        <div className="field-row">
          <label>类型</label>
          <Input size="small" value={trigger.type} disabled />
        </div>

        {trigger.type === 'time' && (
          <div className="field-row">
            <label>时间</label>
            <InputNumber
              size="small"
              style={{ width: '100%' }}
              value={trigger.time}
              addonAfter="s"
              onChange={(v) => updateTrigger(trigger.id, { time: Number(v ?? 0) })}
            />
          </div>
        )}

        {trigger.type === 'location' && (
          <>
            <div className="field-row">
              <label>绑定对象</label>
              <Select
                size="small"
                style={{ width: '100%' }}
                showSearch
                optionFilterProp="label"
                value={trigger.targetAgentId}
                options={agentOptions}
                placeholder="任意 Agent（不限主车）"
                onChange={(v) => updateTrigger(trigger.id, { targetAgentId: v })}
              />
            </div>
            <div className="field-row">
              <label />
              <Button
                size="small"
                type={pendingBindTriggerId === trigger.id ? 'primary' : 'default'}
                block
                onClick={() => {
                  if (pendingBindTriggerId === trigger.id) {
                    armBindTriggerTarget(null);
                    message.info('已取消点选绑定');
                    return;
                  }
                  armBindTriggerTarget(trigger.id);
                  message.info('请点击场景中的 Agent 完成绑定（任意对象）');
                }}
              >
                {pendingBindTriggerId === trigger.id
                  ? '点选中…（再点取消）'
                  : '点选场景对象绑定'}
              </Button>
            </div>
            <div className="route-list-empty" style={{ marginBottom: 8 }}>
              检测谁进入本区域就触发；可选主车 / 行人 / 车辆等任意 Agent。
            </div>
            <div className="field-row">
              <label>长度 Length</label>
              <InputNumber
                size="small"
                style={{ width: '100%' }}
                min={1}
                step={0.1}
                value={trigger.size?.x ?? (trigger.radius ?? 6) * 2}
                addonAfter="m"
                onChange={(v) =>
                  updateTrigger(trigger.id, {
                    size: {
                      x: Number(v ?? 1),
                      y: trigger.size?.y ?? 4.5,
                      z: trigger.size?.z ?? 0.5,
                    },
                  })
                }
              />
            </div>
            <div className="field-row">
              <label>宽度 Width</label>
              <InputNumber
                size="small"
                style={{ width: '100%' }}
                min={1}
                step={0.1}
                value={trigger.size?.y ?? (trigger.radius ?? 6) * 2}
                addonAfter="m"
                onChange={(v) =>
                  updateTrigger(trigger.id, {
                    size: {
                      x: trigger.size?.x ?? 10,
                      y: Number(v ?? 1),
                      z: trigger.size?.z ?? 0.5,
                    },
                  })
                }
              />
            </div>
            <div className="field-row">
              <label>高度 Height</label>
              <InputNumber
                size="small"
                style={{ width: '100%' }}
                min={0.2}
                step={0.1}
                value={trigger.size?.z ?? 0.5}
                addonAfter="m"
                onChange={(v) =>
                  updateTrigger(trigger.id, {
                    size: {
                      x: trigger.size?.x ?? 10,
                      y: trigger.size?.y ?? 4.5,
                      z: Number(v ?? 0.5),
                    },
                  })
                }
              />
            </div>
            <div className="field-row heading-row">
              <label>朝向</label>
              <div className="heading-controls">
                <HeadingDial
                  value={trigger.heading ?? 0}
                  onChange={(rad) => updateTrigger(trigger.id, { heading: rad })}
                />
                <div className="heading-side">
                  <InputNumber
                    size="small"
                    style={{ width: '100%' }}
                    step={1}
                    value={Number((((trigger.heading ?? 0) * 180) / Math.PI).toFixed(1))}
                    addonAfter="°"
                    onChange={(v) =>
                      updateTrigger(trigger.id, {
                        heading: (Number(v ?? 0) * Math.PI) / 180,
                      })
                    }
                  />
                </div>
              </div>
            </div>
            <div className="field-row">
              <label>中心 {apolloCoords ? '(ENU)' : ''}</label>
              <div className="field-inline">
                <InputNumber
                  size="small"
                  value={Number(trigger.center.x.toFixed(apolloCoords ? 3 : 2))}
                  onChange={(v) =>
                    updateTrigger(trigger.id, {
                      center: { ...trigger.center, x: Number(v ?? 0) },
                    })
                  }
                />
                <InputNumber
                  size="small"
                  value={Number(trigger.center.y.toFixed(apolloCoords ? 3 : 2))}
                  onChange={(v) =>
                    updateTrigger(trigger.id, {
                      center: { ...trigger.center, y: Number(v ?? 0) },
                    })
                  }
                />
                <InputNumber size="small" value={trigger.center.z} disabled />
              </div>
            </div>
          </>
        )}

        {trigger.type === 'agent_distance' && (
          <>
            <div className="field-row">
              <label>对象 A</label>
              <Select
                size="small"
                style={{ width: '100%' }}
                value={trigger.agentAId}
                options={agentOptions}
                onChange={(v) => updateTrigger(trigger.id, { agentAId: v })}
              />
            </div>
            <div className="field-row">
              <label>对象 B</label>
              <Select
                size="small"
                style={{ width: '100%' }}
                value={trigger.agentBId}
                options={agentOptions}
                onChange={(v) => updateTrigger(trigger.id, { agentBId: v })}
              />
            </div>
            <div className="field-row">
              <label>比较</label>
              <Select
                size="small"
                style={{ width: '100%' }}
                value={trigger.compare}
                options={[
                  { value: 'less', label: '距离 <' },
                  { value: 'greater', label: '距离 >' },
                ]}
                onChange={(v) => updateTrigger(trigger.id, { compare: v })}
              />
            </div>
            <div className="field-row">
              <label>距离</label>
              <InputNumber
                size="small"
                style={{ width: '100%' }}
                value={trigger.distance}
                addonAfter="m"
                onChange={(v) => updateTrigger(trigger.id, { distance: Number(v ?? 1) })}
              />
            </div>
          </>
        )}

        {trigger.type === 'speed' && (
          <>
            <div className="field-row">
              <label>绑定对象</label>
              <Select
                size="small"
                style={{ width: '100%' }}
                showSearch
                optionFilterProp="label"
                value={trigger.targetAgentId}
                options={agentOptions}
                onChange={(v) => updateTrigger(trigger.id, { targetAgentId: v })}
              />
            </div>
            <div className="field-row">
              <label />
              <Button
                size="small"
                type={pendingBindTriggerId === trigger.id ? 'primary' : 'default'}
                block
                onClick={() => {
                  if (pendingBindTriggerId === trigger.id) {
                    armBindTriggerTarget(null);
                    return;
                  }
                  armBindTriggerTarget(trigger.id);
                  message.info('请点击场景中的 Agent 完成绑定');
                }}
              >
                {pendingBindTriggerId === trigger.id
                  ? '点选中…（再点取消）'
                  : '点选场景对象绑定'}
              </Button>
            </div>
            <div className="field-row">
              <label>比较</label>
              <Select
                size="small"
                style={{ width: '100%' }}
                value={trigger.compare}
                options={[
                  { value: 'greater', label: '速度 >' },
                  { value: 'less', label: '速度 <' },
                ]}
                onChange={(v) => updateTrigger(trigger.id, { compare: v })}
              />
            </div>
            <div className="field-row">
              <label>速度</label>
              <InputNumber
                size="small"
                style={{ width: '100%' }}
                value={trigger.speed}
                addonAfter="m/s"
                onChange={(v) => updateTrigger(trigger.id, { speed: Number(v ?? 0) })}
              />
            </div>
          </>
        )}

        {trigger.type === 'behavior' && (
          <>
            <div className="field-row">
              <label>绑定对象</label>
              <Select
                size="small"
                style={{ width: '100%' }}
                showSearch
                optionFilterProp="label"
                value={trigger.sourceAgentId}
                options={agentOptions}
                onChange={(v) => updateTrigger(trigger.id, { sourceAgentId: v })}
              />
            </div>
            <div className="field-row">
              <label />
              <Button
                size="small"
                type={pendingBindTriggerId === trigger.id ? 'primary' : 'default'}
                block
                onClick={() => {
                  if (pendingBindTriggerId === trigger.id) {
                    armBindTriggerTarget(null);
                    return;
                  }
                  armBindTriggerTarget(trigger.id);
                  message.info('请点击场景中的 Agent 完成绑定');
                }}
              >
                {pendingBindTriggerId === trigger.id
                  ? '点选中…（再点取消）'
                  : '点选场景对象绑定'}
              </Button>
            </div>
            <div className="field-row">
              <label>事件</label>
              <Select
                size="small"
                style={{ width: '100%' }}
                value={trigger.event}
                options={[
                  { value: 'arrived', label: '到达 / 停下 (arrived)' },
                  { value: 'stopped', label: '停止 (stopped)' },
                  { value: 'started', label: '开始移动 (started)' },
                ]}
                onChange={(v) => updateTrigger(trigger.id, { event: v })}
              />
            </div>
          </>
        )}
      </div>

      <div className="attr-block">
        <h4>动作 Actions</h4>
        <div className="route-list-empty" style={{ marginBottom: 8 }}>
          常用：Agent 先 Disable，绑定对象进区域后 Enable（用 Agent 配置初速度）；也可 Set Speed。
          绑定对象可为任意 Agent，不限主车。
        </div>
        {trigger.actions.map((action, idx) => {
          const target = scenario.agents.find((a) => a.id === action.targetAgentId);
          const routeOptions =
            target?.routes.map((r) => ({ value: r.id, label: r.name })) ?? [];
          return (
            <div
              key={`${trigger.id}-act-${idx}`}
              style={{
                border: '1px solid #2a3344',
                borderRadius: 6,
                padding: 8,
                marginBottom: 8,
              }}
            >
              <div className="field-row">
                <label>动作</label>
                <Select
                  size="small"
                  style={{ width: '100%' }}
                  value={action.kind}
                  options={[
                    { value: 'enable', label: 'Enable（启用并开始移动）' },
                    { value: 'disable', label: 'Disable（停用）' },
                    { value: 'set_speed', label: 'Set Speed（设速并启用）' },
                    { value: 'stop', label: 'Stop' },
                    { value: 'start_route', label: 'Start Route（可选）' },
                    { value: 'switch_route', label: 'Switch Route（可选）' },
                  ]}
                  onChange={(v) => updateActionAt(idx, { kind: v })}
                />
              </div>
              <div className="field-row">
                <label>作用对象</label>
                <Select
                  size="small"
                  style={{ width: '100%' }}
                  value={action.targetAgentId}
                  options={agentOptions}
                  onChange={(v) => updateActionAt(idx, { targetAgentId: v })}
                />
              </div>
              {(action.kind === 'start_route' || action.kind === 'switch_route') && (
                <div className="field-row">
                  <label>Route</label>
                  <Select
                    size="small"
                    style={{ width: '100%' }}
                    value={action.routeId || undefined}
                    placeholder="选择路径"
                    options={routeOptions}
                    onChange={(v) => updateActionAt(idx, { routeId: v })}
                  />
                </div>
              )}
              {action.kind === 'set_speed' && (
                <div className="field-row">
                  <label>速度</label>
                  <InputNumber
                    size="small"
                    style={{ width: '100%' }}
                    value={action.speed}
                    addonAfter="m/s"
                    min={0}
                    step={0.1}
                    onChange={(v) => updateActionAt(idx, { speed: Number(v ?? 0) })}
                  />
                </div>
              )}
              <Button
                size="small"
                danger
                block
                onClick={() =>
                  patchActions(trigger.actions.filter((_, i) => i !== idx))
                }
              >
                删除动作
              </Button>
            </div>
          );
        })}
        <Button
          size="small"
          type="dashed"
          block
          onClick={() => {
            const ped = scenario.agents.find((a) => a.type === 'pedestrian');
            const fallback =
              scenario.agents.find((a) => a.type !== 'ego') ?? scenario.agents[0];
            const target = ped ?? fallback;
            if (!target) return;
            patchActions([
              ...trigger.actions,
              { kind: 'enable', targetAgentId: target.id },
            ]);
          }}
        >
          + Add action
        </Button>
      </div>
    </div>
  );
}
