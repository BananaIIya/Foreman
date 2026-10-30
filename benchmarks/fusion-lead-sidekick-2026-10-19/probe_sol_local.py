"""Local protocol probe for the Codex lead, no Daytona and no task: Luna (cents) plays the lead.

Checks that Codex 0.163 accepts the `delegate` dynamic tool, routes its calls to the host handler, waits through a
delegation longer than two minutes, and that usage events price correctly. There is no remote executor; the sandbox
is read-only and the turn is interrupted at once if the model starts any item other than text, reasoning or the
dynamic tool, so no command runs on this machine.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import sol_fusion as sf

DELAY_SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 130
ALLOWED = {'agentMessage', 'reasoning', 'userMessage', 'plan', 'dynamicToolCall'}


async def main():
    out = Path(__file__).resolve().parent / 'raw' / 'probe-sol-local'
    out.mkdir(parents=True, exist_ok=True)
    private = Path(tempfile.mkdtemp(prefix='fusion-sol-probe-', dir='/private/tmp'))
    (private / 'cwd').mkdir()
    home, auth_sha = sf.private_codex_home(private)
    calls, started_items = [], []

    async def handle(params):
        args = params.get('arguments') or {}
        if isinstance(args, str):
            args = json.loads(args or '{}')
        calls.append({'tool': params.get('tool'), 'brief': args.get('brief')})
        if len(calls) == 1:
            await asyncio.sleep(DELAY_SEC)
            text = 'SIDEKICK REPORT (delegation #1):\nSTATUS: done\nDONE: stored the code word.'
        else:
            text = 'SIDEKICK REPORT (delegation #2):\nSTATUS: done\nDONE: the code word you gave earlier was ZEBRA-42.'
        return {'success': True, 'contentItems': [{'type': 'inputText', 'text': text}]}

    def guard(event):
        if event.get('method') == 'item/started':
            kind = event['params']['item'].get('type')
            started_items.append(kind)
            if kind not in ALLOWED:
                return f'probe_guard_stopped_{kind}'
        return None

    lead = sf.CodexLead(home, out, handle, model='gpt-6-luna', effort='medium')
    try:
        await lead.start()
        outcome = await lead.run(None, ('Call the `delegate` tool exactly twice, one call at a time, and use no other tool. '
                                        'First brief: "Remember the code word ZEBRA-42." Second brief, after the first '
                                        'returns: "What was the code word I gave you?" Then reply with the code word '
                                        'from the second report.'),
                                 str(private / 'cwd'), guard=guard,
                                 instructions='You are testing a delegation tool. Follow the user instructions exactly. Do not run commands.')
    finally:
        await lead.close()
        login = sf.return_refreshed_login(home, auth_sha)
    events = sf.read_events(out / 'app-server.events.jsonl')
    texts = [e['params']['item'].get('text') or '' for e in events
             if e.get('method') == 'item/completed' and e['params']['item'].get('type') == 'agentMessage']
    acct = sf.account_codex(events, 'gpt-6-luna')
    checks = {'outcome': outcome, 'delegate_calls': len(calls), 'call_tools': [c['tool'] for c in calls],
              'items_started': started_items, 'final_mentions_code': any('ZEBRA-42' in t for t in texts),
              'delay_sec': DELAY_SEC, 'cost_usd': acct['cost_usd'], 'responses': acct['responses'], 'tokens': acct['tokens'],
              'codex_login': login}
    (out / 'checks.json').write_text(json.dumps(checks, indent=2) + '\n')
    print(json.dumps(checks, indent=2))
    import shutil
    shutil.rmtree(private, ignore_errors=True)


if __name__ == '__main__':
    asyncio.run(main())
