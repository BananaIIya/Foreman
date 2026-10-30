"""Read-only counters for logs in this experiment's owned live sandboxes."""
import os
import asyncio,json,shlex
from pathlib import Path
from daytona import AsyncDaytona
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parent
CODE=r'''
import json
from pathlib import Path
from collections import Counter
out=[]
for p in [Path('/logs/agent/cursor-cli.txt'),*Path('/logs/agent/hybrid').glob('*.jsonl')]:
 if not p.exists():continue
 ev=[]
 for line in p.read_text().splitlines():
  try:ev.append(json.loads(line))
  except ValueError:pass
 out.append({'file':str(p),'bytes':p.stat().st_size,'types':dict(Counter(e.get('type') for e in ev)),
 'public_messages': ['\n'.join(x.get('text','') for x in e.get('message',{}).get('content',[]) if x.get('type')=='text')[-800:] for e in ev if e.get('type')=='assistant'][-2:],
 'completed_tool_types': dict(Counter(k for e in ev if e.get('type')=='tool_call' and e.get('subtype')=='completed' for k in e.get('tool_call',{}) if k.endswith('ToolCall'))),
 'models':[e.get('model') for e in ev if e.get('type')=='system' and e.get('subtype')=='init'],
 'completion':[{'is_error':e.get('is_error'),'usage':e.get('usage')} for e in ev if e.get('type')=='result']})
print(json.dumps(out))
'''
async def main():
 load_dotenv(os.environ["DAYTONA_ENV_FILE"])
 ev=[json.loads(s) for s in (ROOT/'raw/evidence-events.jsonl').read_text().splitlines()]
 own={e['sandbox_id']:e['trial_name'] for e in ev if e.get('event')=='agent_started'}
 async with AsyncDaytona() as d:
  for box in [s async for s in d.list() if s.id in own]:
   r=await box.process.exec('python3 -c '+shlex.quote(CODE),timeout=20)
   print(json.dumps({'trial':own[box.id],'exit_code':r.exit_code,'logs':json.loads(r.result) if r.exit_code==0 else 'read unavailable'}))
if __name__=='__main__':asyncio.run(main())
