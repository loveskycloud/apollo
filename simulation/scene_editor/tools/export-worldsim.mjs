#!/usr/bin/env node
// Node >= 22.18: use the same TypeScript converter as the browser/bridge.
import { readFileSync, writeFileSync } from 'node:fs';
import { buildWorldSimScenarioPayload } from '../src/scenarios/exportWorldSimScenario.ts';

const [input, output] = process.argv.slice(2);
if (!input || !output || process.argv.length !== 4) {
  console.error('Usage: npm run export:worldsim -- input.mineproj.json output.worldsim.scenario.json');
  process.exit(1);
}
try {
  const document = JSON.parse(readFileSync(input, 'utf8'));
  if (document.kind !== 'mine-project' || document.version !== 1 || !document.scenario) {
    throw new Error('Expected a version 1 mine-project file; use the editor to open legacy scenes.');
  }
  const payload = buildWorldSimScenarioPayload(document.scenario);
  // Never overwrite a user's project or an existing export without permission.
  writeFileSync(output, JSON.stringify(payload, null, 2) + '\n', { flag: 'wx' });
  console.log(`WorldSim scene exported: ${output} (map: ${payload.mapId})`);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
