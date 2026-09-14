export type Vec3 = { x: number; y: number; z: number };

export type AgentType =
  | 'ego'
  | 'vehicle'
  | 'pedestrian'
  | 'loader'
  | 'static';

export type TriggerType =
  | 'time'
  | 'location'
  | 'agent_distance'
  | 'speed'
  | 'behavior';

export type TransformMode = 'translate' | 'rotate' | 'scale';

export type ToolMode =
  | 'select'
  | 'place'
  | 'route_edit'
  | 'routing'
  | 'box_select';

export type BehaviorAction =
  | { kind: 'set_speed'; targetAgentId: string; speed: number }
  | { kind: 'start_route'; targetAgentId: string; routeId: string }
  | { kind: 'stop'; targetAgentId: string }
  | { kind: 'switch_route'; targetAgentId: string; routeId: string }
  | { kind: 'enable'; targetAgentId: string }
  | { kind: 'disable'; targetAgentId: string };

export interface Waypoint {
  id: string;
  /** Apollo 地图下为 ENU 坐标（与 SendRouting 一致） */
  position: Vec3;
  /** 选点时拖拽确定的朝向；双向叠道 routing 用 */
  heading?: number;
  speed?: number;
  /** 三次贝塞尔入射控制点（绝对 ENU）；行人 pathType=bezier 时使用 */
  handleIn?: Vec3;
  /** 三次贝塞尔出射控制点（绝对 ENU） */
  handleOut?: Vec3;
}

export type RoutePathType = 'polyline' | 'bezier';

export interface Route {
  id: string;
  name: string;
  /** 缺省 polyline（车辆）；行人默认 bezier */
  pathType?: RoutePathType;
  waypoints: Waypoint[];
}

export interface Agent {
  id: string;
  name: string;
  type: AgentType;
  /** Apollo 地图下为 ENU 坐标（与 SimControl / SendRouting 一致） */
  position: Vec3;
  heading: number;
  speed: number;
  size: Vec3;
  color: string;
  activeRouteId?: string;
  routes: Route[];
  locked?: boolean;
  /**
   * 是否启用（默认 true）。
   * false 时即使有初速度也不运动，需 Trigger enable / set_speed 后才走。
   */
  enabled?: boolean;
}

export interface TimeTrigger {
  id: string;
  name: string;
  type: 'time';
  time: number;
  actions: BehaviorAction[];
  fired?: boolean;
}

export interface LocationTrigger {
  id: string;
  name: string;
  type: 'location';
  center: Vec3;
  /** 区域尺寸：长(X) / 宽(Y) / 高(Z)，单位 m */
  size: Vec3;
  /** 区域朝向（弧度） */
  heading: number;
  /** 兼容旧数据的圆形半径；有 size 时优先用矩形区域 */
  radius?: number;
  targetAgentId: string;
  actions: BehaviorAction[];
  fired?: boolean;
}

export interface AgentDistanceTrigger {
  id: string;
  name: string;
  type: 'agent_distance';
  agentAId: string;
  agentBId: string;
  distance: number;
  compare: 'less' | 'greater';
  actions: BehaviorAction[];
  fired?: boolean;
}

export interface SpeedTrigger {
  id: string;
  name: string;
  type: 'speed';
  targetAgentId: string;
  speed: number;
  compare: 'less' | 'greater';
  actions: BehaviorAction[];
  fired?: boolean;
}

export interface BehaviorTrigger {
  id: string;
  name: string;
  type: 'behavior';
  sourceAgentId: string;
  event: 'arrived' | 'stopped' | 'started';
  actions: BehaviorAction[];
  fired?: boolean;
}

export type Trigger =
  | TimeTrigger
  | LocationTrigger
  | AgentDistanceTrigger
  | SpeedTrigger
  | BehaviorTrigger;

export type LaneTurn = 'NO_TURN' | 'LEFT_TURN' | 'RIGHT_TURN' | 'U_TURN';

export interface MapLane {
  id: string;
  name: string;
  centerline: Vec3[];
  leftBoundary?: Vec3[];
  rightBoundary?: Vec3[];
  width: number;
  successors: string[];
  predecessors: string[];
  /** Apollo lane.turn；缺省按几何推断地面箭头 */
  turn?: LaneTurn;
}

export interface MapNode {
  id: string;
  name: string;
  position: Vec3;
  kind: 'junction' | 'pass' | 'load' | 'unload';
  connectedLanes: string[];
}

export interface HdMap {
  id: string;
  name: string;
  format: 'apollo_base_map' | 'mine_lane_json';
  bounds: { min: Vec3; max: Vec3 };
  lanes: MapLane[];
  nodes: MapNode[];
  meta?: {
    version?: string;
    projection?: string;
    roadCount?: number;
    junctionCount?: number;
    origin?: Vec3;
  };
}

/** Apollo PnC 仿真模块勾选（场景 JSON 持久化） */
export interface SimModulesConfig {
  planning: boolean;
  control: boolean;
  prediction: boolean;
  routing: boolean;
}

export interface Scenario {
  id: string;
  name: string;
  description?: string;
  duration: number;
  agents: Agent[];
  triggers: Trigger[];
  mapId: string;
  simConfig?: SimModulesConfig;
}

export interface PlaybackState {
  playing: boolean;
  time: number;
  speed: number;
  duration: number;
}

export interface RuntimeAgentState {
  position: Vec3;
  heading: number;
  speed: number;
  routeProgress: number;
  moving: boolean;
  /** 运行时是否激活；缺省跟随 Agent.enabled */
  enabled?: boolean;
}

export type SelectedRef =
  | { kind: 'agent'; id: string }
  | { kind: 'trigger'; id: string }
  | { kind: 'route'; agentId: string; routeId: string }
  | null;
