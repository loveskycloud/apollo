import type { Vec3 } from '../core/types';

/**
 * Apollo Sim Bridge v1 协议（WebSocket JSON，见 Apollo-Integration-Plan.md 第 6 节）。
 *
 * 信封：{ type, data: { requestId?, ...payload } }
 * 请求-响应：bridge 原样回显 requestId；无 requestId 的上行消息为主动推送流。
 */

export type ApolloModuleKey = 'planning' | 'control' | 'prediction' | 'routing';

export type PncModules = Record<ApolloModuleKey, boolean>;

export interface RoutingPoint {
  x: number;
  y: number;
  /** 场景路点放置时的车道航向；SendRouting 应原样传给 sim_bridge */
  heading?: number;
}

export interface EgoPose {
  x: number;
  y: number;
  z: number;
  heading: number;
}

export interface TrajPoint {
  x: number;
  y: number;
  z?: number;
  theta?: number;
  v?: number;
  /** 纵向加速度（可选；前端也可由 v/t 估计） */
  a?: number;
  relativeTime?: number;
}

export interface VehicleParamInfo {
  brand?: string;
  length?: number;
  width?: number;
  height?: number;
  /** camelCase（mock / 规范化后）与 snake_case（JsonUtil::ProtoToJson 原样字段）兼容 */
  wheelBase?: number;
  steerRatio?: number;
  maxSteerAngle?: number;
  maxAcceleration?: number;
  maxDeceleration?: number;
  wheel_base?: number;
  steer_ratio?: number;
  max_steer_angle?: number;
  max_acceleration?: number;
  max_deceleration?: number;
  [key: string]: unknown;
}

export interface HmiStatusInfo {
  simRunning: boolean;
  modules: PncModules;
  /** 当前 em profile 名（profiles/ 下目录） */
  currentVehicle?: string;
  currentMap?: string;
  obstacleCount?: number;
  /** bridge 唯一服务 ID（SB-xxxxxx，由 MAC 派生） */
  serviceId?: string;
  /** 'apollo' = 真实算法闭环；'mock' = Mock 动画（仅联调 UI） */
  mode?: 'apollo' | 'mock';
}

export interface PredictionObstacleTraj {
  id: number | string;
  points: TrajPoint[];
}

/** dreamview_plus RequestRoutePath 同构：Apollo routing 中心线（ENU） */
export interface RoutingPathInfo {
  routingTime?: number;
  distance?: number;
  routePath?: Array<{ point?: Array<{ x: number; y: number; z?: number }> }>;
}

export interface ControlCommandInfo {
  steeringPercentage?: number;
  steeringTarget?: number;
  throttle?: number;
  brake?: number;
  speedMps?: number;
}

// ---- 下行（前端 → bridge）payload ----

export interface SimControlData {
  action: 'START' | 'STOP' | 'RESET';
  startPoint?: { x: number; y: number; z: number; heading: number };
}

export interface ObstacleTrajPoint {
  x: number;
  y: number;
  z?: number;
  heading: number;
  speed: number;
  relativeTime: number;
}

export interface ObstaclePose {
  id: string;
  /** scene agent type → perception type mapping on bridge */
  type?: string;
  x: number;
  y: number;
  z: number;
  heading: number;
  speed: number;
  length: number;
  width: number;
  height: number;
  /** Scenario planned path; fake_prediction publishes as GT prediction. */
  trajectory?: ObstacleTrajPoint[];
}

// ---- 信封 ----

export interface ApolloEnvelope<T = Record<string, unknown>> {
  type: string;
  data: T & { requestId?: string };
}

export function newRequestId(): string {
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function egoPoseFromVec3(p: Vec3, heading: number): EgoPose {
  return { x: p.x, y: p.y, z: p.z, heading };
}
