"""Native executors with tool-free specialist code handoffs; no scorer access."""
import base64,contextlib,hashlib,json,shlex,shutil,tempfile
from pathlib import Path,PurePosixPath
from hybrid_agent import HybridAgent,baseline_cost,parse_json
from hybrid_native import Session
from hybrid_cursor import cursor_turn
from shared_workspace import executor,make_home
from v2_runtime.coordinator import Coordinator
from v2_runtime.policy import WORKER_SYSTEM,DECISION,DECISION_SCHEMA,ADVISOR,PATCH_SCHEMA,validate_patch
from v2_runtime.sdk import advisor_turn
ROOT=Path(__file__).resolve().parent.parent

class PatchHybridAgent(HybridAgent):
 async def run(self,instruction,environment,context):
  task=environment.environment_dir.parent.name
  assignment=json.loads((ROOT/'manifest.json').read_text())['assignment'][task]
  self.logs_dir.mkdir(exist_ok=True,parents=True)
  logs=self.logs_dir/'phases';logs.mkdir(exist_ok=True)
  private=Path(tempfile.mkdtemp(prefix='advisor-v2-',dir='/private/tmp'))
  worker_session=None
  images=[]
  for p in sorted(environment.environment_dir.rglob('*')):
   if p.suffix.lower() in ('.png','.jpg','.jpeg') and p.stat().st_size<=4000000:
    mime='image/png' if p.suffix.lower()=='.png' else 'image/jpeg'
    images.append({'type':'image','url':f'data:{mime};base64,'+base64.b64encode(p.read_bytes()).decode(),'detail':'original'})
    if len(images)==2:break
  def emit(name,data):
   (self.logs_dir/(name+'.json')).write_text(json.dumps(data,indent=2)+'\n')
  async def codex(role,phase,model,effort,payload,policy,url=None,session_id=None,cap=None,schema=None):
   home=make_home(private,role,model,effort);work=private/'empty';work.mkdir(exist_ok=True)
   client=Session(home,logs/phase,model,effort)
   try:
    await client.start()
    return await client.turn(url,json.dumps(payload,ensure_ascii=False),policy,'/app' if url else str(work),thread_id=session_id,
      schema=schema,extra_input=images if not url else None,budget_usd=cap,no_tools=url is None)
   finally:await client.close()
  async def excerpts(evidence,preferred=()):
   reported=parse_json(evidence.get('text',''));paths=list(preferred)+reported.get('relevant_paths',[])
   rows=[];seen=set();remaining=100000
   for candidate in paths:
    if not isinstance(candidate,str):continue
    p=PurePosixPath(candidate if candidate.startswith('/') else '/app/'+candidate)
    if '..' in p.parts or not str(p).startswith('/app/') or any(x in p.parts for x in ('.git','.cursor','.agents')):continue
    if str(p) in seen:continue
    seen.add(str(p))
    if len(rows)>=12:break
    local=private/('excerpt-'+hashlib.sha256(str(p).encode()).hexdigest())
    try:
     await environment.download_file(str(p),local)
     data=local.read_text()
     limit=min(30000,remaining);text=data[:limit];remaining-=len(text)
     rows.append({'path':str(p),'text':text,'truncated':len(data)>limit,'original_characters':len(data)})
    except Exception as exc:
     rows.append({'path':str(p),'unavailable':True,'reason':type(exc).__name__})
   return rows
  async def apply(patch):
   validate_patch(patch)
   # The remote helper resolves every destination before writing any content.
   # No generated code is executed here. The worker owns execution/validation.
   manifest=private/'patch.json';manifest.write_text(json.dumps(patch))
   remote='/tmp/advisor-v2-patch.json';await environment.upload_file(manifest,remote)
   script="""import json,pathlib,hashlib
p=json.load(open('/tmp/advisor-v2-patch.json'));rows=[]
for f in p['files']:
 d=pathlib.Path(f['path']);resolved=d.resolve()
 if not str(resolved).startswith('/app/') or any(x in resolved.parts for x in ('.git','.cursor','.agents')):raise ValueError('Unsafe resolved patch path')
 rows.append((d,f['content']))
for d,content in rows:
 d.parent.mkdir(parents=True,exist_ok=True);d.write_text(content)
print(json.dumps({'applied':True,'paths':[str(d) for d,_ in rows],'sha256':{str(d):hashlib.sha256(d.read_bytes()).hexdigest() for d,_ in rows},'checks_executed':False}))
"""
   out=await self.exec_as_agent(environment,command='python -c '+shlex.quote(script))
   if out.return_code:raise RuntimeError('Remote literal patch installation failed')
   return json.loads(out.stdout)
  try:
   async with executor(self,environment,private) as url:
    async def worker(phase,payload,policy,cap):
     nonlocal worker_session
     payload['cost_target_usd']=cap
     if assignment['worker'].startswith('cursor/'):
      r=await cursor_turn(self,environment,logs/phase,WORKER_SYSTEM+'\n'+policy+'\n'+json.dumps(payload,ensure_ascii=False),assignment['worker_effort'])
     else:
      r=await codex('worker',phase,assignment['worker'],assignment['worker_effort'],{'stage_instructions':policy,**payload},WORKER_SYSTEM,url,worker_session,cap)
      worker_session=r['thread_id']
     return r
    async def decision(phase,payload):
     payload['workspace_excerpts']=await excerpts(payload['worker_evidence'])
     r=await codex(phase,phase,'gpt-6-luna','medium',payload,DECISION,schema=DECISION_SCHEMA)
     r['decision']=parse_json(r['evidence']['text'])
     import jsonschema
     jsonschema.validate(r['decision'],DECISION_SCHEMA)
     return r
    async def advisor(phase,payload):
     payload['workspace_excerpts']=await excerpts(payload['worker_evidence'],payload['contract']['paths'])
     payload['response_schema']=PATCH_SCHEMA
     if assignment['advisor'].startswith('cursor/'):
      return await advisor_turn(logs/phase,ADVISOR+'\n'+json.dumps(payload,ensure_ascii=False),assignment['advisor_effort'])
     r=await codex(phase,phase,assignment['advisor'],assignment['advisor_effort'],payload,ADVISOR,schema=PATCH_SCHEMA)
     r['patch']=parse_json(r['evidence']['text']);return r
    coordinator=Coordinator(baseline_cost(task),assignment['worker'],worker,decision,advisor,apply,emit)
    result=await coordinator.run(instruction)
    result.update(task=task,assignment=assignment,instruction_sha256=hashlib.sha256(instruction.encode()).hexdigest(),
      budget_note='Native response boundaries can overshoot targets; all spend counts, and one worker integration survives advisor overspend.')
    emit('hybrid-result',result)
  finally:
   with contextlib.suppress(Exception):await environment.upload_dir(self.logs_dir,str(self.environment_logs_dir))
   shutil.rmtree(private)
