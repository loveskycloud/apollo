import { useEffect, useState } from 'react';
import { Dropdown } from 'antd';
import { Toolbar } from '../components/toolbar/Toolbar';
import { SceneTree } from '../components/scene-tree/SceneTree';
import { AttributesPanel } from '../components/attributes/AttributesPanel';
import { PlaybackBar } from '../components/playback/PlaybackBar';
import { Viewport3D } from '../components/viewport/Viewport3D';
import { LogPanel } from '../components/log/LogPanel';
import { SimConfigPanel } from '../components/apollo/ApolloSimPanel';
import { ProjectMenu } from '../components/project/ProjectMenu';
import { useApolloStore } from '../apollo/apolloStore';
import { useViewStore } from './viewStore';
import { useScenarioStore } from '../core/store';
import { ConfigProvider, theme } from 'antd';

const CAMERA_MENU = [
  { key: 'free', label: '默认视角' },
  { key: 'top', label: '2D 视图' },
  { key: 'chase', label: '跟随主车（第三人称）' },
  { key: 'driver', label: '驾驶视角（第一人称）' },
];

function ViewMenu() {
  const cameraMode = useViewStore((s) => s.cameraMode);
  const items = [
    ...CAMERA_MENU.map((i) => ({
      key: i.key,
      label: cameraMode === i.key ? `✓ ${i.label}` : i.label,
    })),
    { type: 'divider' as const },
    { key: 'fit', label: '适应地图' },
  ];
  return (
    <Dropdown
      menu={{
        items,
        onClick: ({ key }) => {
          if (key === 'fit') useViewStore.getState().requestFit();
          else useViewStore.getState().setCameraMode(key as never);
        },
      }}
      trigger={['click']}
    >
      <span className="menu-item" style={{ cursor: 'pointer' }}>
        视图
      </span>
    </Dropdown>
  );
}

export function AppShell() {
  const [leftTab, setLeftTab] = useState<'scene' | 'assets'>('scene');
  const rightTab = useViewStore((s) => s.rightTab);
  const setRightTab = useViewStore((s) => s.setRightTab);

  // Apollo bridge 默认自动连接（长链接 + 断线重连）
  useEffect(() => {
    useApolloStore.getState().connect();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      const store = useScenarioStore.getState();

      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) store.redo();
        else store.undo();
        return;
      }

      const key = e.key.toLowerCase();
      if (key === 'e') {
        if (store.selectedIds.length === 0 && store.selected?.kind !== 'trigger') return;
        e.preventDefault();
        store.setTransformMode('translate');
        return;
      }
      if (key === 'r') {
        if (store.selectedIds.length === 0 && store.selected?.kind !== 'trigger') return;
        e.preventDefault();
        store.setTransformMode('rotate');
        return;
      }
      if (key === 's' && !e.metaKey && !e.ctrlKey) {
        if (store.selectedIds.length === 0 && store.selected?.kind !== 'trigger') return;
        e.preventDefault();
        store.setTransformMode('scale');
        return;
      }
      if (key === 'q' || key === 'escape') {
        e.preventDefault();
        store.setToolMode('select');
        store.clearTransform();
        return;
      }

      if (e.key === 'Delete' || e.key === 'Backspace') {
        store.deleteSelected();
        return;
      }
      if (e.code === 'Space') {
        e.preventDefault();
        if (store.playback.playing) store.pause();
        else store.play();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const scenario = useScenarioStore((s) => s.scenario);
  const map = useScenarioStore((s) => s.map);
  const backend = useScenarioStore((s) => s.rendererBackend);

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#3b82f6',
          colorBgBase: '#141518',
          colorBgContainer: '#1a1c20',
          colorBorder: '#2c313a',
          borderRadius: 4,
          fontSize: 13,
        },
      }}
    >
      <div className="app-shell">
        <div className="app-top">
          <div className="menubar">
            <ProjectMenu />
            <span className="menu-item">编辑</span>
            <ViewMenu />
            <span className="menu-item">仿真</span>
            <span className="menu-item">回放</span>
            <span className="menu-item">帮助</span>
            <span className="app-title">中国煤科井下仿真调度平台 v1.0.0</span>
          </div>
          <Toolbar />
        </div>

        <div className="app-body">
          <aside className="side-panel left">
            <div className="panel-tabs">
              <button
                className={`panel-tab ${leftTab === 'scene' ? 'active' : ''}`}
                onClick={() => setLeftTab('scene')}
              >
                场景对象
              </button>
              <button
                className={`panel-tab ${leftTab === 'assets' ? 'active' : ''}`}
                onClick={() => setLeftTab('assets')}
              >
                资源库
              </button>
            </div>
            {leftTab === 'scene' ? (
              <SceneTree />
            ) : (
              <div className="panel-body">
                <div className="panel-section-title">Base Map</div>
                <div className="attr-block">
                  <div style={{ color: '#8b93a1', fontSize: 12, lineHeight: 1.6 }}>
                    当前地图：{map?.name ?? '未加载'}
                    <br />
                    格式：apollo_base_map
                    <br />
                    可通过顶栏「项目」加载地图。
                  </div>
                </div>
                <div className="panel-section-title">资产</div>
                <div className="attr-block">
                  <div style={{ color: '#8b93a1', fontSize: 12 }}>
                    矿卡 / 装载机 / 行人 / 静态障碍 / 触发区
                  </div>
                </div>
              </div>
            )}
          </aside>

          <main className="viewport-wrap">
            <Viewport3D />
          </main>

          <aside className="side-panel right">
            <div className="right-stack">
              <div className="panel-tabs">
                <button
                  className={`panel-tab ${rightTab === 'attrs' ? 'active' : ''}`}
                  onClick={() => setRightTab('attrs')}
                >
                  对象属性
                </button>
                <button
                  className={`panel-tab ${rightTab === 'sim' ? 'active' : ''}`}
                  onClick={() => setRightTab('sim')}
                >
                  仿真配置
                </button>
              </div>
              <div className="attr-scroll">
                {rightTab === 'sim' ? <SimConfigPanel /> : <AttributesPanel />}
              </div>
              <LogPanel />
            </div>
          </aside>
        </div>

        <div className="app-bottom">
          <PlaybackBar />
          <div className="status-bar">
            <span>
              坐标系: ENU · 场景: {scenario.name} · Map: {map?.format ?? '-'}
            </span>
            <span>Renderer: {backend.toUpperCase()}</span>
          </div>
        </div>
      </div>
    </ConfigProvider >
  );
}
