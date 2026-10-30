"""Cheap protocol probes for V62 (no task, no workspace): dynamic tool round trip with a slow answer,
Sol no-tools advisor with thread resume, Grok no-tools advisor. Prints costs."""
import asyncio, json, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hybrid_native import account_codex, read_events
from v62_runtime.native import ExecutorSession
from v62_runtime.advisor import NoToolsAdvisorSession, ADVISOR_POLICY, ADVISOR_TOOL
from v2_runtime.sdk import advisor_turn

AUTH = Path.home() / '.codex/auth.json'
OUT = Path(__file__).resolve().parent.parent / 'raw/preflight/v62-protocol-probe'


async def tool_roundtrip(private):
    home = private / 'exec-home'; home.mkdir(mode=0o700); shutil.copyfile(AUTH, home / 'auth.json')
    log = OUT / 'executor'; calls = []
    async def handler(params):
        calls.append(params.get('arguments'))
        await asyncio.sleep(60)  # a realistic advisor latency
        return {'success': True, 'contentItems': [{'type': 'inputText', 'text': 'ADVICE: the answer word is BANANA.'}]}
    s = ExecutorSession(home, log, 'gpt-6-luna', 'medium', tool_handler=handler)
    await s.start()
    try:
        work = private / 'w'; work.mkdir()
        thread = await s.rpc('thread/start', {'model': 'gpt-6-luna', 'allowProviderModelFallback': False, 'approvalPolicy': 'never',
            'sandbox': 'read-only', 'cwd': str(work), 'environments': [], 'dynamicTools': [ADVISOR_TOOL],
            'config': {'model_reasoning_effort': 'medium', 'service_tier': 'default', 'features': {'unified_exec': False, 'shell_tool': False}},
            'experimentalRawEvents': True})
        tid = thread['thread']['id']
        st = await s.rpc('turn/start', {'threadId': tid, 'effort': 'medium', 'input': [{'type': 'text', 'text':
            'Protocol test. Call the advisor tool once with question "word?". Then reply with only the answer word it gives.', 'text_elements': []}]})
        await s.wait_turn(tid, st['turn']['id'])
        await s.follow_up(tid, 'Reply with only the word OK.')
    finally:
        await s.close()
    events = read_events(log / 'app-server.events.jsonl')
    msgs = [e['params']['item'].get('text') for e in events if e.get('method') == 'item/completed' and e['params']['item'].get('type') == 'agentMessage']
    kinds = sorted({e['params']['item'].get('type') for e in events if e.get('method') == 'item/completed'})
    return {'tool_calls': calls, 'messages': msgs, 'item_types': kinds, 'cost_usd': account_codex(events, 'gpt-6-luna')['cost_usd']}


async def sol_resume(private):
    home = private / 'adv-home'; home.mkdir(mode=0o700); shutil.copyfile(AUTH, home / 'auth.json')
    empty = private / 'empty'; empty.mkdir()
    out, thread = [], None
    for i, prompt in enumerate(['ORIGINAL TASK: add two numbers.\nEXECUTOR TRANSCRIPT: computed 2+2=5.\nEXECUTOR QUESTION: is this right?',
                                'EXECUTOR TRANSCRIPT SINCE YOUR LAST ADVICE: recomputed 2+2=4.\nEXECUTOR QUESTION: final check?']):
        c = NoToolsAdvisorSession(home, OUT / f'sol-{i}', 'gpt-6.1-sol', 'high')
        await c.start()
        try:
            r = await c.turn(None, prompt, ADVISOR_POLICY, str(empty), thread_id=thread, no_tools=True)
        finally:
            await c.close()
        thread = r['thread_id']
        out.append({'text': r['evidence']['text'][:300], 'cost_usd': r['usage']['cost_usd'], 'thread': thread})
    return out


async def main():
    OUT.mkdir(parents=True, exist_ok=False)
    private = Path(tempfile.mkdtemp(prefix='v62-probe-', dir='/private/tmp'))
    try:
        res = {'tool_roundtrip': await tool_roundtrip(private), 'sol_resume': await sol_resume(private)}
        g = await advisor_turn(OUT / 'grok', ADVISOR_POLICY + '\n\nORIGINAL TASK: add two numbers.\nEXECUTOR TRANSCRIPT: computed 2+2=5.\nEXECUTOR QUESTION: is this right?', 'high')
        res['grok'] = {'status': g['status'], 'text': g['evidence']['text'][:300], 'cost_usd': g['usage']['cost_usd']}
    finally:
        shutil.rmtree(private)
    (OUT / 'result.json').write_text(json.dumps(res, indent=2) + '\n')
    print(json.dumps(res, indent=2))

asyncio.run(main())
