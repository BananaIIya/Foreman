"""Native specialist interventions in the same remote workspace; no scorer access."""
import base64,contextlib,hashlib,json,shutil,tempfile,uuid
from pathlib import Path,PurePosixPath
from hybrid_agent import HybridAgent,baseline_cost,parse_json
from hybrid_native import Session
from v10_runtime.native import AdvisorSession
from shared_workspace import make_home
from v12_runtime.workspace import executor
from v12_runtime.tunnel import guarded_turn
from v16_runtime.coordinator import Coordinator
from v16_runtime.delivery import execute_delivery, validate_manifest
from v16_runtime.policy import PLAN, PLAN_SCHEMA, preparation_images
from v16_runtime.policy import WORKER_SYSTEM,DECISION,DECISION_SCHEMA,ADVISOR,SOL_ADVISOR
from v12_runtime.sdk import advisor_turn
from v10_runtime.sdk_worker import worker_turn
from v11_runtime.worker import bounded_worker
from v9_runtime.remote import run_task_command
from v9_runtime.environment import environment_manifest
from v9_runtime.representation import bounded_text
ROOT=Path(__file__).resolve().parent.parent

class PatchHybridAgent(HybridAgent):
 def populate_context_post_run(self,context):
  from v9_runtime.metrics import aggregate_phases
  from harbor.models.agent.context import ModelUsage
  totals=aggregate_phases(self.logs_dir/'phases')
  context.cost_usd=totals['cost_usd']
  context.metadata={'mixed_native_usage_complete':totals['complete'],'role_cost_usd':totals['role_cost_usd'],
   'known_cost_lower_bound_usd':totals['known_cost_lower_bound_usd'],
   'cost_basis':'standard token list-price equivalent, not a subscription invoice'}
  if totals['complete']:
   context.model_usage={m:ModelUsage(**row) for m,row in totals['models'].items()}
   for key in ['n_input_tokens','n_cache_tokens','n_output_tokens']:
    setattr(context,key,sum(row[key] for row in totals['models'].values()))
 async def run(self,instruction,environment,context):
  task=environment.environment_dir.parent.name
  assignment=json.loads((ROOT/'manifest.json').read_text())['assignment'][task]
  self.logs_dir.mkdir(exist_ok=True,parents=True)
  logs=self.logs_dir/'phases';logs.mkdir(exist_ok=True)
  private=Path(tempfile.mkdtemp(prefix='advisor-v2-',dir='/private/tmp'))
  worker_session=None
  advisor_session=None
  images=[];image_manifest=[];image_hashes=set();sdk_images=[]
  for p in sorted(environment.environment_dir.rglob('*')):
   if p.suffix.lower() in ('.png','.jpg','.jpeg') and p.stat().st_size<=4000000:
    mime='image/png' if p.suffix.lower()=='.png' else 'image/jpeg'
    digest=hashlib.sha256(p.read_bytes()).hexdigest();image_hashes.add(digest)
    image_manifest.append({'original_name':str(p.relative_to(environment.environment_dir)),'sha256':digest,'attached':True})
    sdk_images.append({'data':base64.b64encode(p.read_bytes()).decode(),'mimeType':mime})
    images.append({'type':'image','url':f'data:{mime};base64,'+base64.b64encode(p.read_bytes()).decode(),'detail':'original'})
    if len(images)==2:break
  def emit(name,data):
   (self.logs_dir/(name+'.json')).write_text(json.dumps(data,indent=2)+'\n')
  async def codex(role,phase,model,effort,payload,policy,url=None,session_id=None,cap=None,schema=None,attach_images=True):
   home=make_home(private,role,model,effort);work=private/'empty';work.mkdir(exist_ok=True)
   client=(AdvisorSession if role=='advisor-session' else Session)(home,logs/phase,model,effort)
   try:
    await client.start()
    if not url:
     payload={**payload,'original_image_attachments':image_manifest} if not session_id else {**payload,'original_images_retained_in_this_task_thread':True}
    async def invoke():
     return await client.turn(url,json.dumps(payload,ensure_ascii=False),policy,'/app' if url else str(work),thread_id=session_id,
       schema=schema,extra_input=images if attach_images and not session_id else None,budget_usd=cap,no_tools=url is None)
    return await guarded_turn(client,tunnel,invoke) if url else await invoke()
   finally:await client.close()
  async def excerpts(evidence,preferred=()):
   reported=parse_json(evidence.get('text',''));paths=reported.get('relevant_paths',[])+list(preferred)
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
     if p.suffix.lower() in ('.png','.jpg','.jpeg'):
      digest=hashlib.sha256(local.read_bytes()).hexdigest()
      rows.append({'path':str(p),'kind':'image','attached_to_native_codex_request':digest in image_hashes,'sha256':digest,'note':'Image content is a visual attachment, not a missing text file.'});continue
     data=local.read_text()
     representation=bounded_text(data,min(48000,remaining));remaining-=len(representation['text'])
     rows.append({'path':str(p),**representation,'sha256':hashlib.sha256(local.read_bytes()).hexdigest()})
    except Exception as exc:
     rows.append({'path':str(p),'unavailable':True,'reason':type(exc).__name__})
   return rows
  try:
   async with executor(self,environment,private) as tunnel:
    url=tunnel.url
    async def worker(phase,payload,policy,cap):
     nonlocal worker_session
     payload['cost_target_usd']=cap
     payload['runtime_environment']=await environment_manifest(self,environment)
     initial_preparation=phase=='worker-discovery'
     worker_images=preparation_images(phase,sdk_images)
     payload['original_images_attached_to_this_worker']=[] if initial_preparation else image_manifest
     payload['original_images_retained_for_advisor']=image_manifest
     if assignment['worker'].startswith('cursor/'):
      async def remote_command(command):
       return await run_task_command(self,environment,command)
      async def native_worker(folder,prompt,effort):
       return await worker_turn(folder,prompt,effort,worker_images)
      r=await bounded_worker(logs/phase,WORKER_SYSTEM+'\n'+policy+'\n'+json.dumps(payload,ensure_ascii=False),assignment['worker_effort'],cap,remote_command,native=native_worker)
     else:
      r=await codex('worker',phase,assignment['worker'],assignment['worker_effort'],{'stage_instructions':policy,**payload},WORKER_SYSTEM,url,worker_session,cap,attach_images=not initial_preparation)
      worker_session=r['thread_id']
     return r
    async def planner(phase,payload,cap):
     payload['runtime_environment']=await environment_manifest(self,environment)
     r=await codex(phase,phase,'gpt-6-luna','medium',payload,PLAN,cap=cap,schema=PLAN_SCHEMA)
     r['work_order']=parse_json(r['evidence']['text'])
     import jsonschema
     jsonschema.validate(r['work_order'],PLAN_SCHEMA)
     return r
    async def decision(phase,payload):
     await tunnel.ensure_ready()
     payload['runtime_environment']=await environment_manifest(self,environment)
     payload['workspace_excerpts']=await excerpts(payload['worker_evidence'])
     r=await codex(phase,phase,'gpt-6-luna','medium',payload,DECISION,schema=DECISION_SCHEMA)
     r['decision']=parse_json(r['evidence']['text'])
     import jsonschema
     jsonschema.validate(r['decision'],DECISION_SCHEMA)
     return r
    async def finalizer(phase,advice):
     contract=advice.get('delivery_contract')
     if not contract:return None
     local=private/('delivery-'+contract['token']+'.json')
     try:
      await environment.download_file(contract['manifest_path'],local)
     except Exception as exc:
      emit('delivery-receipt-'+contract['phase'],{'status':'manifest_unavailable','error_type':type(exc).__name__,'no_model_call':True})
      return None
     try:
      if local.stat().st_size>131072:raise ValueError('Delivery manifest exceeds 128 KiB')
      manifest=validate_manifest(json.loads(local.read_text()),contract['token'])
     except (ValueError,TypeError) as exc:
      emit('delivery-receipt-'+contract['phase'],{'status':'invalid_manifest','error':str(exc),'no_model_call':True})
      return None
     async def remote_command(command):
      return await run_task_command(self,environment,command)
     return await execute_delivery(logs/phase,manifest,contract['token'],remote_command,advice['evidence'])
    async def advisor(phase,payload):
     nonlocal advisor_session
     token=uuid.uuid4().hex
     contract={'phase':phase,'token':token,'manifest_path':'/app/.advisor-delivery-'+token+'.json'}
     payload['delivery_contract']=contract
     emit('delivery-contract-'+phase,contract)
     payload['runtime_environment']=await environment_manifest(self,environment)
     payload['original_image_attachments']=image_manifest
     if assignment['advisor'].startswith('cursor/'):
      async def remote_command(command):
       return await run_task_command(self,environment,command)
      r=await advisor_turn(logs/phase,ADVISOR+'\n'+json.dumps(payload,ensure_ascii=False),assignment['advisor_effort'],payload['cost_target_usd'],remote_command)
     else:
      if advisor_session:
       payload={k:v for k,v in payload.items() if k!='original_task'}
       payload['continuation_of_same_task']=True
      r=await codex('advisor-session',phase,assignment['advisor'],assignment['advisor_effort'],payload,SOL_ADVISOR,url=url,session_id=advisor_session,cap=payload['cost_target_usd'])
      advisor_session=r['thread_id']
      r['evidence']['native_status']=r['status']
      r['evidence']['interrupted']=r.get('interrupted')
     r['delivery_contract']=contract
     return r
    coordinator=Coordinator(baseline_cost(task),assignment['worker'],worker,decision,advisor,emit,planner,finalizer)
    result=await coordinator.run(instruction)
    result.update(task=task,assignment=assignment,instruction_sha256=hashlib.sha256(instruction.encode()).hexdigest(),
      sol_advisor_context='Same-task native thread with remote tools; original task/images retained. No cross-task answer reuse.',
      budget_note='Native response boundaries can overshoot targets; all model spend counts. Native completed edits remain available for grading after overspend; unknown usage cannot qualify for savings.')
    emit('hybrid-result',result)
  finally:
   with contextlib.suppress(Exception):await environment.upload_dir(self.logs_dir,str(self.environment_logs_dir))
   shutil.rmtree(private)
