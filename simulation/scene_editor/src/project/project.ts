import type { HdMap, Scenario } from '../core/types';
import {
  loadApolloBaseMapFromText,
  parseApolloBaseMapJson,
} from '../map/loaders/apolloBaseMap';
import { parseApolloBaseMapText } from '../map/loaders/apolloBaseMapText';

/**
 * 项目模型：场景与地图绑定。地图在「项目 → 新建/打开」时确定，
 * 仿真配置中只可切换车辆，不可切换地图（场景元素坐标依赖地图）。
 */

export interface BuiltinMap {
  id: string;
  /** 实际地图名称（目录/文件名），保存到场景与项目文件中 */
  name: string;
  source: string;
}

export const BUILTIN_MAPS: BuiltinMap[] = [
  {
    id: 'builtin:1haolou_202608241047qh',
    name: '1haolou_202608241047qh',
    source: '/maps/1haolou_202608241047qh/base_map.txt',
  },
  {
    id: 'builtin:apollo_base_map_t204',
    name: 'apollo_base_map_t204',
    source: '/maps/apollo_base_map_t204.json',
  },
];

export interface ProjectFile {
  kind: 'mine-project';
  version: 1;
  name: string;
  /** 地图来源：builtin:<id> 或 'embedded'（文件/bridge 地图内嵌到项目） */
  mapSource: string;
  map: {
    name: string;
    /** 非 builtin 来源时内嵌规范化 HdMap，保证项目自包含可复原 */
    data: HdMap | null;
  };
  scenario: Scenario;
  savedAt: string;
}

export interface RecentProject {
  id: string;
  name: string;
  fileName: string;
  savedAt: string;
  project: ProjectFile;
}

/** 加载内置地图 */
export async function loadBuiltinMap(id: string): Promise<HdMap> {
  const builtin = BUILTIN_MAPS.find((m) => m.id === id);
  if (!builtin) throw new Error(`未知内置地图: ${id}`);
  const res = await fetch(builtin.source);
  if (!res.ok) throw new Error(`内置地图加载失败: ${builtin.source}`);
  const text = await res.text();
  const map = mapFromText(text, builtin.name);
  return map;
}

/** 从上传的地图文本（Apollo txt / JSON）解析 */
export function mapFromText(text: string, name: string): HdMap {
  const trimmed = text.trim();
  return trimmed.startsWith('{')
    ? loadApolloBaseMapFromText(text, name)
    : parseApolloBaseMapText(trimmed, name);
}

/** 从 bridge GetMapElements 响应解析 */
export function mapFromBridgePayload(
  payload: Record<string, unknown>,
  name: string,
): HdMap {
  const mapJson = (payload.map ?? payload) as Parameters<
    typeof parseApolloBaseMapJson
  >[0];
  return parseApolloBaseMapJson(mapJson, name);
}

export function buildProjectFile(
  name: string,
  map: HdMap,
  scenario: Scenario,
  mapSource: string,
): ProjectFile {
  const isBuiltin = mapSource.startsWith('builtin:');
  return {
    kind: 'mine-project',
    version: 1,
    name,
    mapSource,
    map: {
      name: map.name,
      data: isBuiltin ? null : structuredClone(map),
    },
    scenario: structuredClone(scenario),
    savedAt: new Date().toISOString(),
  };
}

export function isProjectFile(obj: unknown): obj is ProjectFile {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    (obj as ProjectFile).kind === 'mine-project'
  );
}

/** 解析打开的文本：项目文件 或 旧版场景 JSON（沿用当前地图） */
export function parseOpenedText(
  text: string,
): { type: 'project'; project: ProjectFile } | { type: 'scenario'; scenario: Scenario } {
  const obj = JSON.parse(text) as unknown;
  if (isProjectFile(obj)) {
    return { type: 'project', project: obj };
  }
  const scenario = obj as Scenario;
  if (!Array.isArray(scenario.agents)) {
    throw new Error('无法识别的文件格式（需要项目文件或场景 JSON）');
  }
  return { type: 'scenario', scenario };
}

/** 打开项目时还原地图：builtin 重新拉取，embedded 直接使用 */
export async function resolveProjectMap(
  project: ProjectFile,
): Promise<HdMap> {
  if (project.mapSource.startsWith('builtin:')) {
    return loadBuiltinMap(project.mapSource);
  }
  if (!project.map.data) {
    throw new Error('项目缺少内嵌地图数据');
  }
  return project.map.data;
}

// ---- 最近项目（localStorage）----

const LS_KEY = 'mine-simulator.recentProjects';
const MAX_RECENTS = 5;

export function listRecentProjects(): RecentProject[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as RecentProject[];
  } catch {
    return [];
  }
}

export function pushRecentProject(
  project: ProjectFile,
  fileName: string,
): void {
  const entry: RecentProject = {
    id: `${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    name: project.name,
    fileName,
    savedAt: project.savedAt,
    project,
  };
  const list = [
    entry,
    ...listRecentProjects().filter(
      (r) => !(r.name === project.name && r.fileName === fileName),
    ),
  ].slice(0, MAX_RECENTS);
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(list));
  } catch {
    // 超出 localStorage 配额（地图内嵌较大）时仅保留最近 1 条
    try {
      localStorage.setItem(LS_KEY, JSON.stringify([entry]));
    } catch {
      /* 忽略：保存到文件仍然有效 */
    }
  }
}

export function removeRecentProject(id: string): void {
  localStorage.setItem(
    LS_KEY,
    JSON.stringify(listRecentProjects().filter((r) => r.id !== id)),
  );
}

/** 下载项目文件（无目录选择时的回退） */
export function downloadProjectFile(project: ProjectFile, fileName: string): void {
  downloadJsonFile(project, fileName);
}

function downloadJsonFile(value: unknown, fileName: string): void {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Runtime exports never replace the editable project's saved file handle. */
export async function saveWorldSimScenarioToDisk(
  payload: Record<string, unknown>, suggestedName: string,
): Promise<string> {
  if (canPickSaveLocation()) {
    const handle = await window.showSaveFilePicker({
      suggestedName,
      types: [{ description: 'WorldSim Scenario JSON', accept: { 'application/json': ['.json'] } }],
    });
    const writable = await handle.createWritable();
    await writable.write(JSON.stringify(payload, null, 2));
    await writable.close();
    return handle.name || suggestedName;
  }
  downloadJsonFile(payload, suggestedName);
  return suggestedName;
}

// ---- File System Access API（保存/另存为可选目录）----

/** 当前会话已选中的可写文件句柄（刷新后失效） */
let currentProjectFileHandle: FileSystemFileHandle | null = null;

export function clearProjectFileHandle(): void {
  currentProjectFileHandle = null;
}

export function getProjectFileHandle(): FileSystemFileHandle | null {
  return currentProjectFileHandle;
}

export function setProjectFileHandle(handle: FileSystemFileHandle | null): void {
  currentProjectFileHandle = handle;
}

function projectAcceptTypes(): FilePickerAcceptType[] {
  return [
    {
      description: 'Mine Project JSON',
      accept: { 'application/json': ['.json', '.mineproj.json'] },
    },
  ];
}

async function writeProjectToHandle(
  handle: FileSystemFileHandle,
  project: ProjectFile,
): Promise<void> {
  const writable = await handle.createWritable();
  await writable.write(JSON.stringify(project, null, 2));
  await writable.close();
}

/** 是否支持系统「另存为」目录/文件选择器 */
export function canPickSaveLocation(): boolean {
  return typeof window !== 'undefined' && typeof window.showSaveFilePicker === 'function';
}

/**
 * 另存为：弹出系统文件/目录选择器；不支持则回退为浏览器下载。
 * @returns 最终文件名
 */
export async function saveProjectToDisk(
  project: ProjectFile,
  suggestedName: string,
  opts?: { forcePicker?: boolean },
): Promise<string> {
  const forcePicker = opts?.forcePicker ?? false;
  const suggested = suggestedName.trim() || '未命名项目.mineproj.json';

  if (canPickSaveLocation()) {
    if (!forcePicker && currentProjectFileHandle) {
      await writeProjectToHandle(currentProjectFileHandle, project);
      return currentProjectFileHandle.name || suggested;
    }
    const handle = await window.showSaveFilePicker!({
      suggestedName: suggested,
      types: projectAcceptTypes(),
    });
    await writeProjectToHandle(handle, project);
    currentProjectFileHandle = handle;
    return handle.name || suggested;
  }

  downloadProjectFile(project, suggested);
  return suggested;
}

/** 打开：系统文件选择器（可保留句柄以便后续「保存」写回） */
export async function openProjectFromDisk(): Promise<{
  text: string;
  fileName: string;
} | null> {
  if (typeof window.showOpenFilePicker !== 'function') {
    return null;
  }
  const [handle] = await window.showOpenFilePicker({
    multiple: false,
    types: projectAcceptTypes(),
  });
  const file = await handle.getFile();
  const text = await file.text();
  // 尽量取得写权限，便于之后「保存」直接覆盖
  try {
    if (typeof handle.requestPermission === 'function') {
      const perm = await handle.requestPermission({ mode: 'readwrite' });
      if (perm === 'granted') {
        currentProjectFileHandle = handle;
      } else {
        currentProjectFileHandle = null;
      }
    } else {
      currentProjectFileHandle = handle;
    }
  } catch {
    currentProjectFileHandle = null;
  }
  return { text, fileName: handle.name || file.name };
}
