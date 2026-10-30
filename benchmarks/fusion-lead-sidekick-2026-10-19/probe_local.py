"""Local mechanics probe, no Daytona and no task: Haiku plays both lead and sidekick (cents).

Checks: (1) the sidekick session persists across delegations (remembers a code word), (2) SDK session cost is
cumulative across queries, (3) a delegate tool call that takes longer than two minutes does not time out the lead,
(4) the context stays small (no claude.ai connectors attached). Commands are never executed: the fake container
answers every bash call with a fixed string.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient, tool

import fusion_arms as fa

DELAY_SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 130


class FakeContainer:
    async def run(self, command):
        return 0, '(probe: commands are not executed)'

    async def plain(self, command, timeout=60):
        return 0, ''

    async def image(self, path):
        raise FileNotFoundError(path)


async def main():
    out = Path(__file__).resolve().parent / 'raw' / 'probe-local'
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='fusion-probe-'))
    (scratch / 'lead').mkdir()
    (scratch / 'side').mkdir()
    lead_log, side_log = fa.EventLog(out / 'lead-events.jsonl'), fa.EventLog(out / 'sidekick-events.jsonl')
    container = FakeContainer()
    sidekick = fa.Sidekick(container, side_log, 'PROBE TASK: mechanics test, no real work.', scratch / 'side')
    lock, calls = asyncio.Lock(), []

    @tool('delegate', fa.DELEGATE_DESCRIPTION, {'brief': str})
    async def delegate(args):
        async with lock:
            if not calls:
                await asyncio.sleep(DELAY_SEC)     # simulate a long delegation
            calls.append(args['brief'])
            report = await sidekick.delegate(args['brief'])
        return {'content': [{'type': 'text', 'text': report}]}

    tools = fa.task_tools(container, lead_log, lock) + [delegate]
    options = fa.options_for(fa.HAIKU, 'You are testing a delegation tool. Follow the user instructions exactly.', tools,
                             scratch / 'lead', 0.5)
    prompt = ('Call `delegate` exactly twice, one call at a time. First brief: "Remember the code word ZEBRA-42. Reply with '
              'STATUS: done and nothing else." Second brief (after the first returns): "What was the code word I gave you '
              'earlier? Answer in the DONE line." Then reply with the code word the sidekick reported.')
    results = []
    async with ClaudeSDKClient(options) as client:
        await client.query(prompt)
        texts, result = await fa.drain(client, lead_log, 'lead')
        results.append(result)
    await sidekick.close()
    side = sidekick.summary()
    checks = {
        'delegations': len(calls),
        'second_report_mentions_code': 'ZEBRA-42' in (side_log.path.read_text().split('"kind": "report", "n": 2')[-1] if len(calls) > 1 else ''),
        'lead_final_mentions_code': any('ZEBRA-42' in t for t in texts),
        'sidekick_costs_by_query': [r.get('total_cost_usd') for r in sidekick.results],
        'sidekick_cost_cumulative': all(b >= a for a, b in zip([r['total_cost_usd'] for r in sidekick.results],
                                                                [r['total_cost_usd'] for r in sidekick.results][1:])),
        'lead_result_subtype': result.get('subtype') if result else None,
        'lead_cost_usd': result.get('total_cost_usd') if result else None,
        'sidekick_cost_usd': side['cost_usd'],
        'models_in_lead': sorted((result or {}).get('model_usage') or {}),
        'delay_sec': DELAY_SEC,
        # Context size is the connector-leak check: ~2k tokens here, ~30k when claude.ai connectors attach.
        'lead_usage': {k: ((result or {}).get('usage') or {}).get(k) for k in
                       ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')},
        'delegation_rows': side['delegations'],
    }
    (out / 'checks.json').write_text(json.dumps(checks, indent=2, default=str) + '\n')
    print(json.dumps(checks, indent=2, default=str))


if __name__ == '__main__':
    asyncio.run(main())
