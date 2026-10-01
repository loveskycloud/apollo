import { WebSocket } from 'ws';
const ws = new WebSocket('ws://localhost:8889/ws');
let id = 0;
const send = (type, data) => ws.send(JSON.stringify({ type, data: { ...data, requestId: 'z' + (++id) } }));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
ws.on('open', async () => {
  send('SetModules', { modules: { planning: true, control: true, prediction: true, routing: true } });
  await sleep(1500);
  send('SimControl', { action: 'START', startPoint: { x: 31.29, y: -0.96, z: 0.4, heading: Math.PI },
    modules: { planning: true, control: true, prediction: true, routing: true } });
  await sleep(800);
  send('SendRouting', { points: [ { x: 24.0, y: -0.5 }, { x: -1.3, y: 2.1 } ] });
  console.log('FLOW-SENT');
  await sleep(3000);
  process.exit(0);
});
ws.on('error', (e) => { console.log('WS-ERR', e.message); process.exit(1); });
