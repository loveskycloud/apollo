import { create } from 'zustand';

export type CameraMode = 'free' | 'top' | 'chase' | 'driver';
export type RightTab = 'attrs' | 'sim';

interface ViewState {
  /** free=默认轨道 | top=2D 正交平面 | chase=第三人称跟随主车 | driver=驾驶视角 */
  cameraMode: CameraMode;
  /** 适应地图请求计数（触发视口 fitToMap） */
  fitTick: number;
  /** 右侧面板标签：对象属性 | 仿真配置（默认挂载仿真配置） */
  rightTab: RightTab;
  setCameraMode: (mode: CameraMode) => void;
  requestFit: () => void;
  setRightTab: (tab: RightTab) => void;
}

export const useViewStore = create<ViewState>((set) => ({
  cameraMode: 'free',
  fitTick: 0,
  rightTab: 'sim',
  setCameraMode: (mode) => set({ cameraMode: mode }),
  requestFit: () => set((s) => ({ fitTick: s.fitTick + 1 })),
  setRightTab: (tab) => set({ rightTab: tab }),
}));
