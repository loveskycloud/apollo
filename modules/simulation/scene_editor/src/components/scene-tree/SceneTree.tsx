import { useMemo, useState } from 'react';
import { Tree } from 'antd';
import type { DataNode } from 'antd/es/tree';
import { SearchOutlined } from '@ant-design/icons';
import { useScenarioStore } from '../../core/store';

export function SceneTree() {
  const scenario = useScenarioStore((s) => s.scenario);
  const selected = useScenarioStore((s) => s.selected);
  const select = useScenarioStore((s) => s.select);
  const [keyword, setKeyword] = useState('');

  const match = (name: string) =>
    !keyword || name.toLowerCase().includes(keyword.toLowerCase());

  const treeData: DataNode[] = useMemo(() => {
    const agents = scenario.agents.filter((a) => match(a.name));
    const triggers = scenario.triggers.filter((t) => match(t.name));
    return [
      {
        key: 'ego',
        title: 'Ego Mine Truck',
        children: agents
          .filter((a) => a.type === 'ego')
          .map((a) => ({ key: `agent:${a.id}`, title: a.name })),
      },
      {
        key: 'vehicle',
        title: 'Vehicle Agents',
        children: agents
          .filter((a) => a.type === 'vehicle')
          .map((a) => ({ key: `agent:${a.id}`, title: a.name })),
      },
      {
        key: 'loader',
        title: 'Loader Agents',
        children: agents
          .filter((a) => a.type === 'loader')
          .map((a) => ({ key: `agent:${a.id}`, title: a.name })),
      },
      {
        key: 'pedestrian',
        title: 'Pedestrian Agents',
        children: agents
          .filter((a) => a.type === 'pedestrian')
          .map((a) => ({ key: `agent:${a.id}`, title: a.name })),
      },
      {
        key: 'static',
        title: 'Static Obstacles',
        children: agents
          .filter((a) => a.type === 'static')
          .map((a) => ({ key: `agent:${a.id}`, title: a.name })),
      },
      {
        key: 'triggers',
        title: 'Triggers',
        children: [
          {
            key: 'trig-time',
            title: 'Time Trigger',
            children: triggers
              .filter((t) => t.type === 'time')
              .map((t) => ({ key: `trigger:${t.id}`, title: t.name })),
          },
          {
            key: 'trig-loc',
            title: '区域触发器 (Location)',
            children: triggers
              .filter((t) => t.type === 'location')
              .map((t) => ({ key: `trigger:${t.id}`, title: t.name })),
          },
          {
            key: 'trig-dist',
            title: 'Distance Trigger',
            children: triggers
              .filter((t) => t.type === 'agent_distance')
              .map((t) => ({ key: `trigger:${t.id}`, title: t.name })),
          },
          {
            key: 'trig-speed',
            title: 'Speed Trigger',
            children: triggers
              .filter((t) => t.type === 'speed')
              .map((t) => ({ key: `trigger:${t.id}`, title: t.name })),
          },
          {
            key: 'trig-beh',
            title: 'Behavior Trigger',
            children: triggers
              .filter((t) => t.type === 'behavior')
              .map((t) => ({ key: `trigger:${t.id}`, title: t.name })),
          },
        ],
      },
      {
        key: 'map',
        title: 'Base Map',
        children: [
          { key: 'map-lanes', title: 'Lanes / Roads', selectable: false },
          { key: 'map-junctions', title: 'Junctions', selectable: false },
        ],
      },
    ];
  }, [scenario, keyword]);

  const selectedKeys =
    selected?.kind === 'agent'
      ? [`agent:${selected.id}`]
      : selected?.kind === 'trigger'
        ? [`trigger:${selected.id}`]
        : selected?.kind === 'route'
          ? [`agent:${selected.agentId}`]
          : [];

  return (
    <>
      <div className="panel-search">
        <SearchOutlined className="search-icon" />
        <input
          placeholder="搜索场景对象..."
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
      </div>
      <div className="panel-body">
        <Tree
          treeData={treeData}
          defaultExpandAll
          selectedKeys={selectedKeys}
          onSelect={(keys) => {
            const key = String(keys[0] ?? '');
            if (key.startsWith('agent:')) {
              useScenarioStore.getState().setSelectedIds([key.slice(6)]);
            }
            if (key.startsWith('trigger:')) {
              select({ kind: 'trigger', id: key.slice(8) });
            }
          }}
        />
      </div>
    </>
  );
}
