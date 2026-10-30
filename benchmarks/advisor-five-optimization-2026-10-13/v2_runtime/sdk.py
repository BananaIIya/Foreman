import asyncio,json
from pathlib import Path
from routing import usage_cost
from hybrid_agent import parse_json
ROOT=Path(__file__).resolve().parent
async def advisor_turn(folder,prompt,effort):
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
 (folder/'request.json').write_text(json.dumps({'model':'grok-4.7','provider':'cursor-sdk','effort':effort,'prompt':prompt,'tools':[],'sdk_version':'1.0.37'},indent=2)+'\n')
 with (folder/'sdk-console.txt').open('w') as log:
  p=await asyncio.create_subprocess_exec('/opt/homebrew/bin/node',str(ROOT/'cursor_sdk/advisor.mjs'),str(folder),stdout=log,stderr=log)
  code=await p.wait()
 if code:raise RuntimeError('Cursor SDK failed; inspect phase console without exposing credentials')
 raw=json.loads((folder/'sdk-result.json').read_text())
 native=raw['usage'];text=raw.get('result') or ''
 # One no-tools native answer; usage events and final counters must reconcile.
 for key in ['inputTokens','outputTokens','cacheReadTokens','cacheWriteTokens']:
  if sum(u[key] for u in raw['usageEvents'])!=native[key]:raise RuntimeError('SDK usage counters do not reconcile')
 result={'status':'completed' if raw['status']=='finished' else raw['status'],'native_model':raw['model'],
  'usage':{'native_usage':native,'cost_usd':usage_cost('grok-4.7',native,'cursor')},
  'evidence':{'text':text,'commands':[],'command_count':0},'patch':parse_json(text)}
 (folder/'phase-result.json').write_text(json.dumps(result,indent=2)+'\n')
 return result
