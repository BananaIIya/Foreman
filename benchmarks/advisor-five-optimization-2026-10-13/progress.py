"""Read-only native event counters; never exposes private thinking blocks."""
import os
import asyncio
import json
import shlex
from pathlib import Path
from daytona import AsyncDaytona
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
CODE = r'''
import json
from pathlib import Path
from collections import Counter
p=Path('/logs/agent/cursor-cli.txt')
if not p.exists():
 print(json.dumps({'phase':'setting_up'}));raise SystemExit
events=[]
for line in p.read_text().splitlines():
 try:events.append(json.loads(line))
 except ValueError:pass
inits=[x for x in events if x.get('type')=='system' and x.get('subtype')=='init']
public=[x for x in events if x.get('type')=='assistant']
results=[x for x in events if x.get('type')=='result']
tools=[x for x in events if x.get('type')=='tool_call' and x.get('subtype')=='completed']
latest_tools=[]
for x in tools[-4:]:
 tool=x.get('tool_call',{})
 for name, detail in tool.items():
  if not name.endswith('ToolCall'):continue
  args=detail.get('args',{}) if isinstance(detail,dict) else {}
  latest_tools.append({'tool':name,'command':str(args.get('command',''))[:220],
   'path':args.get('path',args.get('target_file')),'url':args.get('url')})
print(json.dumps({'log_bytes':p.stat().st_size,'events':dict(Counter(x.get('type') for x in events)),
 'init':[{'model':x.get('model'),'session_id':x.get('session_id'),'cwd':x.get('cwd')} for x in inits],
 'latest_public_message':('\n'.join(t.get('text','') for t in public[-1].get('message',{}).get('content',[]) if t.get('type')=='text')[-650:] if public else None),
 'latest_completed_tools':latest_tools,
 'recent_transport_events':[{k:x.get(k) for k in ['type','status','attempt','maxAttempts','delayMs','message','reason']} for x in events if x.get('type') in ['connection','retry']][-4:],
 'completion':[{'subtype':x.get('subtype'),'is_error':x.get('is_error'),'usage':x.get('usage')} for x in results]}))
'''


async def main():
    load_dotenv(os.environ["DAYTONA_ENV_FILE"])
    starts = []
    p = ROOT / 'raw/evidence-events.jsonl'
    if p.exists():
        starts = [json.loads(s) for s in p.read_text().splitlines() if json.loads(s).get('event') == 'agent_started']
    own_ids = {x['sandbox_id']: x for x in starts}
    async with AsyncDaytona() as d:
        for sandbox in [s async for s in d.list() if s.id in own_ids]:
            result = await sandbox.process.exec('python3 -c ' + shlex.quote(CODE), timeout=20)
            if result.exit_code:
                print(json.dumps({'sandbox_id':sandbox.id,'read_exit_code':result.exit_code}))
            else:
                state = json.loads(result.result)
                state['trial_name'] = own_ids[sandbox.id]['trial_name']
                print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
