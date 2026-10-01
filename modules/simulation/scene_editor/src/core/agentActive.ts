/** Agent 是否处于激活态（编辑 enabled 或运行时已 Enable / 正在运动） */
export function isAgentActive(
  agent: { enabled?: boolean },
  rt?: { enabled?: boolean; moving?: boolean; speed?: number } | null,
): boolean {
  if (rt?.enabled !== undefined) return rt.enabled;
  // 旧 WorldSim 不下发 enabled 时：运动起来即视为激活
  if (rt?.moving) return true;
  return agent.enabled !== false;
}
