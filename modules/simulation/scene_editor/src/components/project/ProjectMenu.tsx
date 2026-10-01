import { useEffect, useState } from 'react';
import { Dropdown, Input, Modal, Radio, Upload, message } from 'antd';
import { useApolloStore } from '../../apollo/apolloStore';
import { useScenarioStore } from '../../core/store';
import type { HdMap } from '../../core/types';
import { buildWorldSimScenarioPayload } from '../../scenarios/exportWorldSimScenario';
import {
  BUILTIN_MAPS,
  buildProjectFile,
  canPickSaveLocation,
  clearProjectFileHandle,
  listRecentProjects,
  loadBuiltinMap,
  mapFromBridgePayload,
  mapFromText,
  openProjectFromDisk,
  parseOpenedText,
  pushRecentProject,
  removeRecentProject,
  resolveProjectMap,
  saveProjectToDisk,
  saveWorldSimScenarioToDisk,
  type RecentProject,
} from '../../project/project';

function makeFileName(name: string): string {
  const safe = name.trim().replace(/[\\/:*?"<>|\s]+/g, '_') || '未命名项目';
  return `${safe}.mineproj.json`;
}

function currentMap(): NonNullable<ReturnType<typeof useScenarioStore.getState>['map']> {
  const map = useScenarioStore.getState().map;
  if (!map) throw new Error('当前未加载地图');
  return map;
}

/** 保存（有文件句柄则写回；否则走另存为选择目录） */
export function saveProject() {
  void doSave({ forcePicker: false });
}

/** 另存为（总是弹出系统目录/文件选择器；不支持时回退下载） */
export function saveProjectAs() {
  void doSave({ forcePicker: true });
}

async function exportWorldSim() {
  try {
    const scenario = useScenarioStore.getState().scenario;
    const payload = buildWorldSimScenarioPayload(scenario);
    const name = makeFileName(scenario.name).replace(/\.mineproj\.json$/, '.worldsim.scenario.json');
    const savedName = await saveWorldSimScenarioToDisk(payload, name);
    message.success(`WorldSim 场景已导出：${savedName}。请在仿真任务中选择此文件，地图选择 ${scenario.mapId}；不要选择项目文件。`, 10);
  } catch (err) {
    if ((err as { name?: string })?.name === 'AbortError') return;
    message.error(`场景导出失败：${String(err)}`);
  }
}

async function doSave(opts: { forcePicker: boolean }) {
  try {
    const store = useScenarioStore.getState();
    const project = store.exportProjectFile();
    const suggested = makeFileName(project.name);
    const finalName = await saveProjectToDisk(project, suggested, {
      forcePicker: opts.forcePicker,
    });
    pushRecentProject(project, finalName);
    localStorage.setItem('mine-simulator.hasFileName', finalName);
    message.success(
      canPickSaveLocation()
        ? `项目已保存：${finalName}`
        : `已下载：${finalName}（当前浏览器不支持目录选择，请改用 Chrome/Edge）`,
    );
  } catch (err) {
    const e = err as { name?: string };
    if (e?.name === 'AbortError') return;
    message.error(`保存失败：${String(err)}`);
  }
}

/** 打开项目文本（项目文件或旧版场景 JSON） */
export async function openProjectText(text: string, fileName?: string) {
  const parsed = parseOpenedText(text);
  const store = useScenarioStore.getState();
  if (parsed.type === 'project') {
    const map = await resolveProjectMap(parsed.project);
    store.openProjectFile(parsed.project, map);
    localStorage.setItem(
      'mine-simulator.hasFileName',
      fileName ?? `${parsed.project.name}.mineproj.json`,
    );
    message.success(`项目已打开：${parsed.project.name}（地图：${map.name}）`);
    return;
  }
  // 旧版场景 JSON：沿用当前地图
  clearProjectFileHandle();
  const scenario = { ...parsed.scenario, mapId: currentMap().id };
  store.openProjectFile(
    buildProjectFile(scenario.name, currentMap(), scenario, store.projectMapSource),
    currentMap(),
  );
  message.success(`场景已打开：${scenario.name}（沿用当前地图）`);
}

async function openWithSystemPicker() {
  try {
    const picked = await openProjectFromDisk();
    if (!picked) {
      message.warning('当前浏览器不支持系统打开对话框，请使用下方「打开项目文件」');
      return;
    }
    await openProjectText(picked.text, picked.fileName);
  } catch (err) {
    const e = err as { name?: string };
    if (e?.name === 'AbortError') return;
    message.error(`打开失败：${String(err)}`);
  }
}

function NewProjectModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [name, setName] = useState('未命名项目');
  const [source, setSource] = useState<string>(BUILTIN_MAPS[0].id);
  const [bridgeMaps, setBridgeMaps] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const connected = useApolloStore((s) => s.status) === 'connected';

  useEffect(() => {
    if (!open) return;
    setName('未命名项目');
    setSource(BUILTIN_MAPS[0].id);
    const apollo = useApolloStore.getState();
    if (apollo.status === 'connected') {
      void apollo
        .request('GetMapList')
        .then((d) => setBridgeMaps((d.maps as string[]) ?? []))
        .catch(() => setBridgeMaps([]));
    }
  }, [open]);

  const create = async () => {
    setBusy(true);
    try {
      let map: HdMap;
      let mapSource: string;
      if (source.startsWith('builtin:')) {
        map = await loadBuiltinMap(source);
        mapSource = source;
      } else if (source.startsWith('bridge:')) {
        const mapName = source.slice('bridge:'.length);
        const payload = await useApolloStore
          .getState()
          .request('GetMapElements', { map: mapName });
        map = mapFromBridgePayload(payload, mapName);
        mapSource = 'embedded';
      } else {
        const text = sessionStorage.getItem('mine-simulator.uploadMapText') ?? '';
        map = mapFromText(text, name || '上传地图');
        mapSource = 'embedded';
      }
      useScenarioStore.getState().newProject(name.trim() || '未命名项目', map, mapSource);
      useApolloStore.setState({ trajectory: [], egoPose: null });
      clearProjectFileHandle();
      localStorage.removeItem('mine-simulator.hasFileName');
      message.success(
        `项目已创建：${name}（地图：${map.name}）。请在工具栏选择「主车」后在视口点击放置。`,
      );
      onClose();
    } catch (err) {
      message.error(`创建失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="新建项目"
      open={open}
      onCancel={onClose}
      onOk={() => void create()}
      okText="创建"
      okButtonProps={{ loading: busy }}
      cancelText="取消"
      width={480}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <div style={{ marginBottom: 4, fontSize: 12, color: '#8b93a1' }}>项目名称</div>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="项目名称" />
        </div>
        <div>
          <div style={{ marginBottom: 4, fontSize: 12, color: '#8b93a1' }}>
            选择地图（场景将基于该地图创建；仿真配置中不可更换地图）
          </div>
          <Radio.Group
            value={source}
            onChange={(e) => setSource(e.target.value)}
            style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
          >
            {BUILTIN_MAPS.map((m) => (
              <Radio key={m.id} value={m.id}>
                {m.name}
              </Radio>
            ))}
            {bridgeMaps.map((m) => (
              <Radio key={`bridge:${m}`} value={`bridge:${m}`}>
                从 Apollo bridge 加载：{m}
              </Radio>
            ))}
            <Radio value="upload">
              <Upload
                accept=".txt,.json"
                showUploadList={false}
                beforeUpload={(file) => {
                  void file.text().then((t) => {
                    sessionStorage.setItem('mine-simulator.uploadMapText', t);
                    setSource('upload');
                    message.info(`地图文件已读取：${file.name}`);
                  });
                  return false;
                }}
              >
                <span>上传地图文件（Apollo base_map.txt / JSON）</span>
              </Upload>
            </Radio>
          </Radio.Group>
          {!connected && (
            <div style={{ fontSize: 11, color: '#8b93a1', marginTop: 4 }}>
              连接 Apollo bridge 后可从其地图库选择。
            </div>
          )}
          <div style={{ fontSize: 11, color: '#8b93a1', marginTop: 8 }}>
            新建后不自动放置主车；请用工具栏「主车」在地图上点击添加（坐标为 Apollo ENU）。
          </div>
        </div>
      </div>
    </Modal>
  );
}

function OpenProjectModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [recents, setRecents] = useState<RecentProject[]>([]);

  useEffect(() => {
    if (open) setRecents(listRecentProjects());
  }, [open]);

  const openRecent = async (entry: RecentProject) => {
    try {
      clearProjectFileHandle();
      const map = await resolveProjectMap(entry.project);
      useScenarioStore.getState().openProjectFile(entry.project, map);
      useApolloStore.setState({ trajectory: [], egoPose: null });
      localStorage.setItem('mine-simulator.hasFileName', entry.fileName);
      message.success(`项目已打开：${entry.project.name}`);
      onClose();
    } catch (err) {
      message.error(`打开失败：${String(err)}`);
    }
  };

  return (
    <Modal
      title="打开项目"
      open={open}
      onCancel={onClose}
      footer={null}
      width={480}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {typeof window.showOpenFilePicker === 'function' && (
          <button
            className="tool-btn"
            type="button"
            onClick={() =>
              void openWithSystemPicker().then(() => onClose())
            }
          >
            选择项目文件…
          </button>
        )}
        <Upload
          accept=".json,application/json"
          showUploadList={false}
          beforeUpload={(file) => {
            clearProjectFileHandle();
            void file
              .text()
              .then((t) => openProjectText(t, file.name))
              .then(() => onClose())
              .catch((err) => message.error(`打开失败：${String(err)}`));
            return false;
          }}
        >
          <button className="tool-btn" type="button">打开项目文件（.json）</button>
        </Upload>
        <div>
          <div style={{ fontSize: 12, color: '#8b93a1', marginBottom: 6 }}>最近项目（本机）</div>
          {recents.length === 0 && (
            <div style={{ fontSize: 12, color: '#64748b' }}>暂无最近项目</div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {recents.map((r) => (
              <div
                key={r.id}
                style={{ display: 'flex', alignItems: 'center', gap: 8 }}
              >
                <button
                  className="tool-btn"
                  type="button"
                  style={{ flex: 1, textAlign: 'left' }}
                  onClick={() => void openRecent(r)}
                >
                  {r.name}
                  <span style={{ color: '#64748b', marginLeft: 8, fontSize: 10 }}>
                    {new Date(r.savedAt).toLocaleString()}
                  </span>
                </button>
                <button
                  className="tool-btn"
                  type="button"
                  onClick={() => {
                    removeRecentProject(r.id);
                    setRecents(listRecentProjects());
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

/** 顶栏「项目」菜单：新建 / 打开 / 保存 / 另存为 */
export function ProjectMenu() {
  const [newOpen, setNewOpen] = useState(false);
  const [openOpen, setOpenOpen] = useState(false);

  const items = [
    { key: 'new', label: '新建项目…' },
    { key: 'open', label: '打开项目…' },
    { type: 'divider' as const },
    { key: 'save', label: '保存' },
    { key: 'saveAs', label: '另存为…' },
    { type: 'divider' as const },
    { key: 'exportWorldSim', label: '导出 WorldSim 场景…' },
  ];

  const onMenuClick = ({ key }: { key: string }) => {
    if (key === 'new') setNewOpen(true);
    if (key === 'open') setOpenOpen(true);
    if (key === 'save') saveProject();
    if (key === 'saveAs') saveProjectAs();
    if (key === 'exportWorldSim') void exportWorldSim();
  };

  return (
    <>
      <Dropdown menu={{ items, onClick: onMenuClick }} trigger={['click']}>
        <span className="menu-item" style={{ cursor: 'pointer' }}>项目</span>
      </Dropdown>
      <NewProjectModal open={newOpen} onClose={() => setNewOpen(false)} />
      <OpenProjectModal open={openOpen} onClose={() => setOpenOpen(false)} />
    </>
  );
}
