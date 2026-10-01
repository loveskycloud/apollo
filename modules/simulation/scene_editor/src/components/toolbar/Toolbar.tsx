import type { ReactNode } from 'react';
import {
  ApiOutlined,
  CarOutlined,
  CloudOutlined,
  DeleteOutlined,
  EnvironmentOutlined,
  NodeIndexOutlined,
  PlayCircleOutlined,
  RedoOutlined,
  SaveOutlined,
  StopOutlined,
  ThunderboltOutlined,
  UndoOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Tooltip, message } from 'antd';
import type { AgentType, TriggerType } from '../../core/types';
import { useScenarioStore } from '../../core/store';
import { useApolloStore } from '../../apollo/apolloStore';
import { useViewStore } from '../../app/viewStore';
import { saveProject } from '../project/ProjectMenu';

function ToolBtn({
  title,
  active,
  primary,
  green,
  onClick,
  children,
}: {
  title: string;
  active?: boolean;
  primary?: boolean;
  green?: boolean;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip title={title}>
      <button
        className={`tool-btn ${active ? 'active' : ''} ${primary ? 'primary' : ''} ${green ? 'connected' : ''}`}
        onClick={onClick}
        type="button"
      >
        {children}
      </button>
    </Tooltip>
  );
}

export function Toolbar() {
  const toolMode = useScenarioStore((s) => s.toolMode);
  const placeType = useScenarioStore((s) => s.placeType);
  const transformMode = useScenarioStore((s) => s.transformMode);
  const transformActive = useScenarioStore((s) => s.transformActive);
  const selectedIds = useScenarioStore((s) => s.selectedIds);
  const selected = useScenarioStore((s) => s.selected);
  const setToolMode = useScenarioStore((s) => s.setToolMode);
  const setTransformMode = useScenarioStore((s) => s.setTransformMode);
  const clearTransform = useScenarioStore((s) => s.clearTransform);
  const undo = useScenarioStore((s) => s.undo);
  const redo = useScenarioStore((s) => s.redo);
  const deleteSelected = useScenarioStore((s) => s.deleteSelected);
  const play = useScenarioStore((s) => s.play);
  const pause = useScenarioStore((s) => s.pause);
  const stop = useScenarioStore((s) => s.stop);
  const playing = useScenarioStore((s) => s.playback.playing);

  const connected = useApolloStore((s) => s.status) === 'connected';
  const setRightTab = useViewStore((s) => s.setRightTab);

  const hasSelection = selectedIds.length > 0 || selected?.kind === 'trigger';

  const ensureSelectionForTransform = () => {
    if (!hasSelection) {
      message.warning('请先选中物体或区域触发器，再按 E/R/S');
      return false;
    }
    return true;
  };

  const placeAgent = (type: AgentType) => {
    if (type === 'ego' && useScenarioStore.getState().scenario.agents.some((a) => a.type === 'ego')) {
      message.warning('场景中已有主车，请先删除再建');
      return;
    }
    setToolMode('place', type);
    message.info(type === 'ego' ? '点击视口放置主车' : `点击视口放置 ${type}`);
  };

  const placeTrigger = (type: TriggerType) => {
    if (type === 'time' || type === 'speed' || type === 'behavior') {
      useScenarioStore.getState().addTrigger(type);
      return;
    }
    setToolMode('place', type);
    message.info(type === 'location' ? '点击视口放置区域触发器' : `点击视口放置 ${type} 触发器`);
  };

  return (
    <div className="toolbar">
      <div className="tool-group">
        <ToolBtn title="保存项目" onClick={saveProject}>
          <SaveOutlined />
        </ToolBtn>
      </div>

      <div className="tool-sep" />

      <div className="tool-group">
        <ToolBtn title="撤销" onClick={undo}>
          <UndoOutlined />
        </ToolBtn>
        <ToolBtn title="重做" onClick={redo}>
          <RedoOutlined />
        </ToolBtn>
        <ToolBtn title="删除" onClick={() => deleteSelected()}>
          <DeleteOutlined />
        </ToolBtn>
      </div>

      <div className="tool-sep" />

      <div className="tool-group">
        <ToolBtn title="主车" active={placeType === 'ego'} onClick={() => placeAgent('ego')}>
          主车
        </ToolBtn>
        <ToolBtn title="矿卡" active={placeType === 'vehicle'} onClick={() => placeAgent('vehicle')}>
          <CarOutlined /> 矿卡
        </ToolBtn>
        <ToolBtn
          title="行人"
          active={placeType === 'pedestrian'}
          onClick={() => placeAgent('pedestrian')}
        >
          <UserOutlined /> 行人
        </ToolBtn>
        <ToolBtn title="装载机" active={placeType === 'loader'} onClick={() => placeAgent('loader')}>
          <ThunderboltOutlined /> 装载机
        </ToolBtn>
        <ToolBtn title="静态障碍" active={placeType === 'static'} onClick={() => placeAgent('static')}>
          障碍
        </ToolBtn>
        <ToolBtn
          title="区域触发器"
          active={placeType === 'location'}
          onClick={() => placeTrigger('location')}
        >
          区域
        </ToolBtn>
        <ToolBtn title="距离触发器" onClick={() => placeTrigger('agent_distance')}>
          距离
        </ToolBtn>
        <ToolBtn title="时间触发器" onClick={() => placeTrigger('time')}>
          时间
        </ToolBtn>
        <ToolBtn title="粉尘区" onClick={() => message.info('环境区：后续扩展')}>
          <CloudOutlined /> 粉尘
        </ToolBtn>
      </div>

      <div className="tool-sep" />

      <div className="tool-group">
        <ToolBtn
          title="选择 (Q / Esc)"
          active={toolMode === 'select' && !transformActive}
          onClick={() => {
            setToolMode('select');
            clearTransform();
          }}
        >
          选择
        </ToolBtn>
        <ToolBtn
          title="移动 (E) — 需先选中物体"
          active={transformActive && transformMode === 'translate'}
          onClick={() => {
            if (!ensureSelectionForTransform()) return;
            setTransformMode('translate');
          }}
        >
          移动E
        </ToolBtn>
        <ToolBtn
          title="旋转 (R) — 需先选中物体"
          active={transformActive && transformMode === 'rotate'}
          onClick={() => {
            if (!ensureSelectionForTransform()) return;
            setTransformMode('rotate');
          }}
        >
          旋转R
        </ToolBtn>
        <ToolBtn
          title="缩放 (S) — 需先选中物体"
          active={transformActive && transformMode === 'scale'}
          onClick={() => {
            if (!ensureSelectionForTransform()) return;
            setTransformMode('scale');
          }}
        >
          缩放S
        </ToolBtn>
      </div>

      <div className="tool-sep" />

      <div className="tool-group">
        <ToolBtn
          title="路径编辑"
          active={toolMode === 'route_edit'}
          onClick={() => setToolMode('route_edit')}
        >
          <NodeIndexOutlined /> 路径
        </ToolBtn>
        <ToolBtn
          title="沿车道选起终点"
          active={toolMode === 'routing'}
          onClick={() => {
            useScenarioStore.getState().setRoutingStart(null);
            setToolMode('routing');
            message.info('在车道内依次点选起点与终点');
          }}
        >
          <EnvironmentOutlined /> Routing
        </ToolBtn>
      </div>

      <div className="tool-spacer" />

      <div className="tool-group">
        <ToolBtn
          title={connected ? '已连接 Apollo bridge' : '未连接 Apollo bridge'}
          green={connected}
          onClick={() => setRightTab('sim')}
        >
          <ApiOutlined /> {connected ? '已连接' : '未连接'}
        </ToolBtn>
      </div>

      <div className="tool-group">
        <ToolBtn title="停止" onClick={stop}>
          <StopOutlined />
        </ToolBtn>
        <ToolBtn title="运行" primary onClick={() => (playing ? pause() : play())}>
          <PlayCircleOutlined /> {playing ? '暂停' : '运行'}
        </ToolBtn>
      </div>
    </div>
  );
}
