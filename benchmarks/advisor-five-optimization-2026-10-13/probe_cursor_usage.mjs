// Read-only billing lookup for this experiment's existing agents. No inference.
import fs from 'node:fs';
import path from 'node:path';
import {Agent} from './v2_runtime/cursor_sdk/node_modules/@cursor/sdk/dist/esm/index.js';

const root = process.cwd();
const apiKey = JSON.parse(fs.readFileSync(process.env.CURSOR_AUTH_JSON, 'utf8')).apiKey;
const inputs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const results = [];
for (const input of inputs) {
  const events = fs.readFileSync(path.join(root, input.events), 'utf8').trim().split('\n').flatMap(line => {
    try { return [JSON.parse(line)]; } catch { return []; }
  });
  const agentId = events.find(event => typeof event.agent_id === 'string')?.agent_id;
  if (!agentId) { results.push({name: input.name, available: false, reason: 'no_agent_id'}); continue; }
  let timer;
  try {
    const usage = await Promise.race([
      Agent.getUsage(agentId, {apiKey}),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('query_deadline')), 20000); }),
    ]);
    results.push({name: input.name, agentId, available: true, usage});
  } catch (error) {
    results.push({name: input.name, agentId, available: false, errorName: error.name, errorCode: error.code ?? null});
  } finally { clearTimeout(timer); }
}
const output = {read_only: true, inference_calls: 0, at: new Date().toISOString(), results};
fs.writeFileSync(process.argv[3], JSON.stringify(output, null, 2) + '\n');
console.log(JSON.stringify(output));
process.exit(0);
