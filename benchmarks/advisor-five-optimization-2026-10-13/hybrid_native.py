"""Native Codex sessions with explicit remote execution and per-turn accounting."""
import asyncio
import json
from pathlib import Path
from native_remote import NativeRemote
from routing import usage_cost


def read_events(path):
    result = []
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            try:
                result.append(json.loads(line))
            except ValueError:
                pass
    return result


def account_codex(events, model, turn_id=None):
    seen = set()
    costs = []
    usage = []
    for e in events:
        if e.get('method') != 'thread/tokenUsage/updated':
            continue
        p = e['params']; u = p['tokenUsage']
        if turn_id is not None and p.get('turnId') != turn_id:
            continue
        key = (p['threadId'], p.get('turnId'), json.dumps(u['total'], sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        usage.append(u['last'])
        costs.append(usage_cost(model, u['last'], 'codex'))
    return {'responses': len(usage), 'per_response_usage': usage,
            'cost_usd': sum(costs) if costs and all(c is not None for c in costs) else None}


def public_evidence(events):
    items = [e['params']['item'] for e in events if e.get('method') == 'item/completed']
    streamed = {}
    for e in events:
        if e.get('method') == 'item/commandExecution/outputDelta':
            p = e['params']
            streamed[p['itemId']] = streamed.get(p['itemId'], '') + p.get('delta', '')
    text = '\n\n'.join(i.get('text') or '' for i in items if i.get('type') == 'agentMessage')
    commands = [{'command': i.get('command'), 'exit_code': i.get('exitCode'),
                 'output': (i.get('aggregatedOutput') or streamed.get(i['id'], ''))[-4000:],
                 'output_available': i.get('aggregatedOutput') is not None or i['id'] in streamed}
                for i in items if i.get('type') == 'commandExecution']
    return {'text': text, 'commands': commands[-10:], 'command_count': len(commands)}


class Session(NativeRemote):
    async def turn(self, url, prompt, policy, cwd, thread_id=None, schema=None,
                   extra_input=None, budget_usd=None, no_tools=False):
        env = []
        if url:
            await self.rpc('environment/add', {'environmentId': 'daytona', 'execServerUrl': url, 'connectTimeoutMs': 30000})
            env = [{'environmentId': 'daytona', 'cwd': cwd, 'runtimeWorkspaceRoots': [cwd]}]
        params = {
            'model': self.model, 'approvalPolicy': 'never', 'cwd': cwd,
            'sandbox': 'danger-full-access' if url else 'read-only',
            'environments': env,
            'config': {'model_reasoning_effort': self.effort, 'service_tier': 'default',
                       'features': {'unified_exec': True, 'multi_agent': False}, 'web_search': 'disabled'},
        }
        if thread_id:
            params['threadId'] = thread_id
            t = await self.rpc('thread/resume', params)
        else:
            params.update(allowProviderModelFallback=False, experimentalRawEvents=True,
                          developerInstructions=policy)
            t = await self.rpc('thread/start', params)
            thread_id = t['thread']['id']
        assert t['model'] == self.model and t['reasoningEffort'] == self.effort
        turn_params = {'threadId': thread_id, 'effort': self.effort, 'cwd': cwd,
                       'environments': env,
                       'input': [{'type': 'text', 'text': prompt, 'text_elements': []}] + (extra_input or [])}
        if schema:
            turn_params['outputSchema'] = schema
        (self.log_dir / 'request.json').write_text(json.dumps({
            'model': self.model, 'effort': self.effort, 'thread_id': thread_id,
            'prompt': prompt, 'policy': policy, 'budget_usd': budget_usd,
            'remote_executor': bool(url), 'no_tools': no_tools}, indent=2) + '\n')
        started = await self.rpc('turn/start', turn_params)
        turn_id = started['turn']['id']
        observations = []
        interrupted = None
        while True:
            e = await asyncio.wait_for(self.notifications.get(), 1800)
            observations.append(e)
            if e.get('method', '').startswith('transport/'):
                raise RuntimeError('Native Codex transport closed during active turn')
            if e.get('method') == 'item/started' and no_tools:
                kind = e['params']['item']['type']
                if kind not in ('agentMessage', 'reasoning', 'userMessage', 'plan') and interrupted is None:
                    interrupted = 'decision_model_attempted_tool'
                    await self.rpc('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
            if e.get('method') == 'thread/tokenUsage/updated' and budget_usd is not None and interrupted is None:
                spend = account_codex(observations, self.model, turn_id)['cost_usd']
                if spend is not None and spend > budget_usd:
                    interrupted = 'budget_boundary_exceeded'
                    try:
                        await self.rpc('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
                    except RuntimeError:
                        # The final usage event can race with natural turn completion.
                        # Keep consuming the actual completed event; all spend counts.
                        pass
            if e.get('method') == 'turn/completed' and e['params']['turn']['id'] == turn_id:
                status = e['params']['turn']['status']
                break
        events = read_events(self.log_dir / 'app-server.events.jsonl')
        result = {'thread_id': thread_id, 'turn_id': turn_id, 'status': status, 'interrupted': interrupted,
                  'usage': account_codex(events, self.model, turn_id), 'evidence': public_evidence(events)}
        (self.log_dir / 'phase-result.json').write_text(json.dumps(result, indent=2) + '\n')
        if no_tools and interrupted:
            raise RuntimeError(interrupted)
        return result
