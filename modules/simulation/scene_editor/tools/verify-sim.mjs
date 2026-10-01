#!/usr/bin/env node
/**
 * 验证 sim_bridge 仿真：连接 → START → SendRouting → 观测 Ego 移动与 WS 流。
 * 用法：node tools/verify-sim.mjs
 */
import { WebSocket } from 'ws';
import { spawn } from 'node:child_process';
import { TEST_ROUTE_1HAOLOU } from './testRoute.mjs';

const WS_URL = process.env.SIM_WS ?? 'ws://127.0.0.1:8889/ws';
const OBSERVE_SEC = Number(process.env.OBSERVE_SEC ?? 25);
const BRIDGE_PID_CMD =
  'docker exec apollo_neo_dev_10.0.0_pkg pgrep -x sim_bridge || echo GONE';

const START = {
  ...TEST_ROUTE_1HAOLOU.ego.position,
  heading: TEST_ROUTE_1HAOLOU.ego.heading,
};
const ROUTE = TEST_ROUTE_1HAOLOU.waypoints.map((p) => ({ x: p.x, y: p.y }));

let reqId = 0;
const pending = new Map();
const stats = {
  ego: 0,
  plan: 0,
  hmi: 0,
  maxV: 0,
  maxDelta: 0,
  planPts: 0,
  errors: [],
  simRunning: false,
  bridgeChecks: [],
};

function checkBridge(tag) {
  return new Promise((resolve) => {
    const child = spawn('bash', ['-lc', BRIDGE_PID_CMD]);
    let out = '';
    child.stdout.on('data', (d) => { out += d; });
    child.on('close', () => {
      const alive = /^\d+/m.test(out.trim());
      stats.bridgeChecks.push({ tag, alive, out: out.trim().split('\n')[0] ?? '' });
      resolve(alive);
    });
  });
}

function request(ws, type, data = {}, timeoutMs = 90000) {
  const requestId = `v${++reqId}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(requestId);
      reject(new Error(`timeout ${type}`));
    }, timeoutMs);
    pending.set(requestId, { resolve, reject, timer });
    ws.send(JSON.stringify({ type, data: { ...data, requestId } }));
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let lastPose = null;

function dist(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

async function main() {
  console.log('=== sim_bridge 验证 ===');
  console.log('WS:', WS_URL);
  await checkBridge('before');

  const ws = new WebSocket(WS_URL);
  ws.on('message', (raw) => {
    let msg;
    try {
      msg = JSON.parse(String(raw));
    } catch {
      return;
    }
    const rid = msg?.data?.requestId;
    if (rid && pending.has(rid)) {
      const p = pending.get(rid);
      pending.delete(rid);
      clearTimeout(p.timer);
      p.resolve(msg.data);
      return;
    }
    if (msg.type === 'EgoState') {
      stats.ego++;
      const v = msg.data.speed ?? 0;
      stats.maxV = Math.max(stats.maxV, v);
      const pose = { x: msg.data.x, y: msg.data.y };
      if (lastPose) stats.maxDelta = Math.max(stats.maxDelta, dist(lastPose, pose));
      lastPose = pose;
    } else if (msg.type === 'PlanningTrajectory') {
      stats.plan++;
      stats.planPts = Math.max(stats.planPts, msg.data.points?.length ?? 0);
    } else if (msg.type === 'HmiStatus') {
      stats.hmi++;
      stats.simRunning = !!msg.data.simRunning;
    } else if (msg.type === 'Error') {
      stats.errors.push(String(msg.data.message ?? JSON.stringify(msg.data)));
    }
  });

  await new Promise((resolve, reject) => {
    ws.on('open', resolve);
    ws.on('error', reject);
  });
  console.log('connected');

  const modules = { planning: true, control: true, prediction: true, routing: true };
  try {
    await request(ws, 'SimControl', { action: 'STOP' }, 30000);
  } catch {
    /* ignore */
  }
  await sleep(1500);

  const start = await request(
    ws,
    'SimControl',
    { action: 'START', startPoint: START, modules },
    90000,
  );
  console.log('START:', JSON.stringify(start));
  await sleep(4000);
  await checkBridge('after-start');

  const routing = await request(ws, 'SendRouting', { points: ROUTE }, 30000);
  console.log('SendRouting:', JSON.stringify(routing).slice(0, 280));

  console.log(`observing ${OBSERVE_SEC}s…`);
  const step = 5;
  for (let t = 0; t < OBSERVE_SEC; t += step) {
    await sleep(step * 1000);
    await checkBridge(`t+${t + step}s`);
    process.stdout.write(
      `\r  t=${t + step}s ego=${stats.ego} maxV=${stats.maxV.toFixed(3)} delta=${stats.maxDelta.toFixed(3)} planPts=${stats.planPts}`,
    );
  }
  console.log('\n');

  ws.close();
  await checkBridge('after');

  const bridgeStable = stats.bridgeChecks.every((c) => c.alive);
  const moved = stats.maxDelta > 0.5 || stats.maxV > 0.15;
  const planOk = stats.planPts > 0;

  console.log('\n=== 结果 ===');
  console.log('simRunning:', stats.simRunning);
  console.log('EgoState 帧数:', stats.ego, '最大速度:', stats.maxV.toFixed(4), 'm/s');
  console.log('最大位移:', stats.maxDelta.toFixed(4), 'm');
  console.log('PlanningTrajectory 最大点数:', stats.planPts);
  console.log('Errors:', stats.errors.length ? stats.errors : 'none');
  console.log('sim_bridge 存活检查:');
  for (const c of stats.bridgeChecks) {
    console.log(`  [${c.tag}] ${c.alive ? 'OK' : 'DEAD'} ${c.out}`);
  }

  const pass = bridgeStable && moved && planOk && routing.accepted !== false;
  console.log('\nVERDICT:', pass ? 'PASS' : 'FAIL');
  if (!bridgeStable) console.log('  - sim_bridge 进程异常退出');
  if (!moved) console.log('  - 车辆未明显移动');
  if (!planOk) console.log('  - 未收到有效规划轨迹');
  if (routing.accepted === false) console.log('  - SendRouting 被拒绝');

  process.exit(pass ? 0 : 1);
}

main().catch((e) => {
  console.error('FATAL', e.message);
  process.exit(2);
});
