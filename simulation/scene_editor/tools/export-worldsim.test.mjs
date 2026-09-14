import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildWorldSimScenarioPayload as build } from '../src/scenarios/exportWorldSimScenario.ts';

const point = { x: 9998.5477, y: 9000003.4294, z: 0.4 };
function scene() {
  const actor = {
    id: 'ego', name: 'Ego', type: 'ego', position: point, heading: Math.PI,
    speed: 2, size: { x: 1, y: .5, z: .8 }, color: '#ff0', locked: true,
    activeRouteId: 'route', routes: [{ id: 'route', name: 'Route', waypoints: [
      { id: 'wp', position: point },
      { id: 'wp2', position: point, heading: 0, speed: 0, handleIn: point, handleOut: point },
    ] }],
  };
  return {
    id: 'test', name: 'test', mapId: 'map', duration: 60,
    simConfig: { planning: true, control: true, prediction: true, routing: true },
    agents: [actor, { ...structuredClone(actor), id: 'walker', type: 'pedestrian', enabled: false }],
    triggers: [],
  };
}

test('splits ego, preserves ENU/route/settings and excludes editor metadata without mutating input', () => {
  const input = scene();
  const original = structuredClone(input);
  const out = build(input);
  assert.deepEqual(input, original);
  assert.equal(out.ego.heading, Math.PI);
  assert.deepEqual(out.ego.position, point);
  assert.deepEqual(out.simConfig, input.simConfig);
  assert.equal(out.duration, 60);
  assert.equal(out.agents.length, 1);
  assert.equal(out.agents[0].type, 'AGENT_TYPE_PEDESTRIAN');
  assert.equal(out.agents[0].enabled, false);
  assert.equal(out.agents[0].routes[0].pathType, 'bezier');
  assert.equal(out.ego.routes[0].pathType, 'polyline');
  assert.equal('locked' in out.ego, false);
  assert.equal('locked' in out.agents[0], false);
  assert.equal('heading' in out.ego.routes[0].waypoints[0], false);
  assert.equal(out.ego.routes[0].waypoints[1].heading, 0);
  assert.equal(out.ego.routes[0].waypoints[1].speed, 0);
  assert.deepEqual(out.ego.routes[0].waypoints[1].handleIn, point);
});

test('maps all supported triggers and actions to protobuf enum names', () => {
  const input = scene();
  const actions = [
    { kind: 'set_speed', speed: 3 }, { kind: 'start_route', routeId: 'route' },
    { kind: 'stop' }, { kind: 'switch_route', routeId: 'route' },
    { kind: 'enable' }, { kind: 'disable' },
  ].map((a) => ({ ...a, targetAgentId: 'walker' }));
  input.triggers = [
    { type: 'time', time: 1 },
    { type: 'location', center: point, size: point, heading: 1, radius: 2, targetAgentId: 'walker' },
    { type: 'agent_distance', agentAId: 'ego', agentBId: 'walker', distance: 2, compare: 'less' },
    { type: 'speed', targetAgentId: 'walker', speed: 1, compare: 'greater' },
    { type: 'behavior', sourceAgentId: 'walker', event: 'arrived' },
  ].map((t, i) => ({ ...t, id: String(i), name: String(i), fired: true, actions }));
  const out = build(input);
  for (let i = 0; i < input.triggers.length; i++) {
    const { fired: _fired, type, ...rest } = input.triggers[i];
    assert.deepEqual(out.triggers[i], {
      ...rest, type: `TRIGGER_TYPE_${type.toUpperCase()}`,
      actions: actions.map((a) => ({ ...a, kind: `ACTION_${a.kind.toUpperCase()}` })),
    });
  }
});

test('rejects missing/duplicate ego, IDs, invalid routes and nonfinite numbers', () => {
  for (const mutate of [
    (s) => s.agents.shift(),
    (s) => s.agents.push(structuredClone(s.agents[0])),
    (s) => { s.agents[1].id = 'ego'; },
    (s) => { s.agents[0].activeRouteId = 'missing'; },
    (s) => { s.duration = -1; },
    (s) => { s.agents[0].heading = Infinity; },
  ]) {
    const input = scene();
    mutate(input);
    assert.throws(() => build(input));
  }
});

test('does not silently coerce unsupported agent, trigger or action types', () => {
  const input = scene();
  input.agents[1].type = 'unknown';
  assert.throws(() => build(input), /不支持/);
  input.agents[1].type = 'vehicle';
  input.triggers = [{ id: 't', type: 'unknown', actions: [] }];
  assert.throws(() => build(input), /不支持/);
  input.triggers = [{ id: 't', type: 'time', time: 1, actions: [{ kind: 'unknown' }] }];
  assert.throws(() => build(input), /不支持/);
});
