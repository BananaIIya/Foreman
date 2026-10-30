"""Native specialist interventions in the same remote workspace; no scorer access."""
import base64,contextlib,copy,hashlib,json,shutil,tempfile,tomllib,uuid
from pathlib import Path,PurePosixPath
from hybrid_agent import HybridAgent,baseline_cost,parse_json
from hybrid_native import Session, read_events
from v32_runtime.context_delta import resume_delta
from v33_runtime.reviewer import ReviewerSession, reviewed_decision
from v40_runtime.native import AdvisorSession
from shared_workspace import make_home
from v12_runtime.workspace import executor
from v12_runtime.tunnel import guarded_turn
from v43_runtime.coordinator import Coordinator
from v36_runtime.context import independent_request
from v38_runtime.evidence_index import publish as publish_evidence_index
from v32_runtime.evidence import compact_payload
from v32_runtime.delivery import execute_delivery, validate_manifest
from v32_runtime.snapshots import capture_snapshot
from v43_runtime.policy import PLAN, PLAN_SCHEMA, preparation_images
from v43_runtime.policy import WORKER_SYSTEM,DECISION,DECISION_SCHEMA,ADVISOR,SOL_ADVISOR
from v12_runtime.sdk import advisor_turn
from v10_runtime.sdk_worker import worker_turn
from v32_runtime.worker import bounded_worker
from v9_runtime.remote import run_task_command
from v9_runtime.environment import environment_manifest
from v34_runtime.progress import collect_progress
from v32_runtime.representation import artifact_excerpt
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
  artifact_sources=tomllib.loads((ROOT/'tasks'/task/'task.toml').read_text()).get('artifacts',[])
  self.logs_dir.mkdir(exist_ok=True,parents=True)
  logs=self.logs_dir/'phases';logs.mkdir(exist_ok=True)
  private=Path(tempfile.mkdtemp(prefix='advisor-v2-',dir='/private/tmp'))
  worker_session=None
  advisor_session=None
  advisor_memory=None
  delivery_token=uuid.uuid4().hex
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
   client=(AdvisorSession if role=='advisor-session' else ReviewerSession if url is None else Session)(home,logs/phase,model,effort)
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
   rows=[];seen=set();remaining=12000
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
     representation=artifact_excerpt(str(p),local.read_bytes(),min(4000,remaining))
     remaining-=len(representation.get('text',''))
     rows.append(representation)
    except Exception as exc:
     rows.append({'path':str(p),'unavailable':True,'reason':type(exc).__name__})
   return rows
  try:
   async with executor(self,environment,private) as tunnel:
    url=tunnel.url
    runner_source=ROOT/'v43_runtime/component_runner.py'
    component_contract={'module_path':'/app/worker_component.py',
      'runner_path':'/app/.worker_component_runner.py',
      'receipt_path':'/app/.worker-component-executions.jsonl',
      'interface':'run(payload: dict, output_dir: str) -> {files: [produced file paths]}',
      'invocation':'python3 /app/.worker_component_runner.py --input CURRENT_TASK_JSON --output-dir OUTPUT_DIRECTORY --purpose synthetic_check|original_task',
      'runner_sha256':hashlib.sha256(runner_source.read_bytes()).hexdigest(),
      'correctness_proven':False}
    await environment.upload_file(runner_source,component_contract['runner_path'])
    emit('worker-component-contract',dict(component_contract,uploaded=True,model_calls=0))
    async def collect_component(stage):
     for label,remote in [('module',component_contract['module_path']),('receipts',component_contract['receipt_path'])]:
      local=self.logs_dir/('worker-component-'+stage+'-'+label+('.py' if label=='module' else '.jsonl'))
      try:
       await environment.download_file(remote,local)
       emit('worker-component-'+stage+'-'+label,{'status':'available','source':remote,
         'local_file':local.name,'sha256':hashlib.sha256(local.read_bytes()).hexdigest(),
         'model_calls':0,'correctness_proven':False})
      except Exception as exc:
       emit('worker-component-'+stage+'-'+label,{'status':'unavailable','source':remote,
         'error_type':type(exc).__name__,'model_calls':0,'correctness_proven':False})
    async def worker(phase,payload,policy,cap):
     nonlocal worker_session
     payload=compact_payload(payload)
     payload['cost_target_usd']=cap
     payload['runtime_environment']=await environment_manifest(self,environment)
     payload['runtime_component']=component_contract
     initial_preparation=phase=='worker-discovery' or phase.startswith('worker-preparation-')
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
     await collect_component(phase)
     return r
    async def planner(phase,payload,cap):
     payload['runtime_environment']=await environment_manifest(self,environment)
     payload['runtime_component']=component_contract
     r=await codex(phase,phase,'gpt-6-luna','medium',payload,PLAN,cap=cap,schema=PLAN_SCHEMA)
     r['work_order']=parse_json(r['evidence']['text'])
     import jsonschema
     jsonschema.validate(r['work_order'],PLAN_SCHEMA)
     return r
    async def decision(phase,payload):
     await tunnel.ensure_ready()
     payload['runtime_environment']=await environment_manifest(self,environment)
     payload['runtime_component']=component_contract
     payload['workspace_excerpts']=await excerpts(payload['worker_evidence'])
     payload=compact_payload(payload)
     async def invoke():
      return await codex(phase,phase,'gpt-6-luna','medium',payload,DECISION,schema=DECISION_SCHEMA)
     r=await reviewed_decision(invoke,logs/phase)
     if 'decision' not in r:r['decision']=parse_json(r['evidence']['text'])
     import jsonschema
     jsonschema.validate(r['decision'],DECISION_SCHEMA)
     return r
    async def deliver_artifacts(phase,advice):
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
    async def finalizer(phase,advice):
     delivered=await deliver_artifacts(phase,advice)
     contract=advice.get('delivery_contract',{})
     advisor_phase=contract.get('phase')
     if advisor_phase:
      try:
       # Original task metadata, not an inferred output path or a hidden grader.
       # Snapshot bytes stay host-side and are never returned to any model.
       await capture_snapshot(self.logs_dir/'artifact-snapshots'/advisor_phase,
         artifact_sources,environment.download_file,
         {'task':task,'advisor_phase':advisor_phase,
          'executor_phase':phase if delivered is not None else None,
          'delivery_token':contract.get('token'),
          'all_commands_succeeded':delivered['execution']['all_commands_succeeded'] if delivered else None})
      except Exception as exc:
       emit('artifact-snapshot-error-'+advisor_phase,{'error_type':type(exc).__name__,
         'diagnostic_only':True,'model_calls':0,'grader_calls':0})
     return delivered
    async def advisor(phase,payload):
     nonlocal advisor_session,advisor_memory
     payload=compact_payload(payload)
     token=delivery_token
     contract={'phase':phase,'token':token,'scope':'task','repeat_after_advisor':True,
               'manifest_path':'/app/.advisor-delivery-'+token+'.json'}
     payload['delivery_contract']=contract
     emit('delivery-contract-'+phase,contract)
     progress_contract={'phase':phase,'token':token,
                        'path':'/app/.advisor-progress-'+token+'-'+phase+'.json'}
     payload['progress_contract']=progress_contract
     emit('progress-contract-'+phase,progress_contract)
     payload['runtime_environment']=await environment_manifest(self,environment)
     payload['original_image_attachments']=image_manifest
     payload['runtime_component']=component_contract
     if assignment['advisor'].startswith('cursor/'):
      async def remote_command(command):
       return await run_task_command(self,environment,command)
      r=await advisor_turn(logs/phase,ADVISOR+'\n'+json.dumps(payload,ensure_ascii=False),assignment['advisor_effort'],payload['cost_target_usd'],remote_command)
     else:
      payload,receipt=independent_request(payload,advisor_session)
      if receipt:
       emit('independent-context-'+phase,receipt)
       original_payload=payload
       try:
        payload,index_receipt=await publish_evidence_index(payload,
         read_events(logs/advisor_memory['phase']/'app-server.events.jsonl'),
         self.logs_dir,token,phase,environment.upload_file)
        emit('evidence-index-'+phase,{**index_receipt,'status':'available','source_phase':advisor_memory['phase']})
       except Exception as exc:
        payload=original_payload
        emit('evidence-index-'+phase,{'status':'unavailable','error_type':type(exc).__name__,
         'fallback':'Original self-contained public evidence retained','extra_model_calls':0})
      r=await codex('advisor-session',phase,assignment['advisor'],assignment['advisor_effort'],payload,SOL_ADVISOR,url=url,session_id=None,cap=payload['cost_target_usd'])
      if receipt:
       if r['thread_id']==advisor_session:raise RuntimeError('Corrective advisor reused the prior thread')
       emit('independent-context-'+phase,{**receipt,'new_thread_id':r['thread_id']})
      advisor_session=r['thread_id']
      r['evidence']['native_status']=r['status']
      r['evidence']['interrupted']=r.get('interrupted')
      events=read_events(logs/phase/'app-server.events.jsonl')
      compacted=any('compact' in str(e.get('method','')).lower() or
        'compact' in str(e.get('params',{}).get('item',{}).get('type','')).lower() for e in events)
      advisor_memory={'thread_id':r['thread_id'],'turn_id':r['turn_id'],'phase':phase,
                      'compacted':compacted,'evidence':copy.deepcopy(r['evidence'])}
     r['delivery_contract']=contract
     progress=await collect_progress(progress_contract,self.logs_dir/('expert-progress-'+phase+'.raw.json'),environment.download_file)
     emit('expert-progress-'+phase,progress)
     if progress['status']=='available':r['expert_progress']=progress['report']
     return r
    coordinator=Coordinator(baseline_cost(task),assignment['worker'],worker,decision,advisor,emit,planner,finalizer)
    result=await coordinator.run(instruction)
    await collect_component('final')
    result.update(task=task,assignment=assignment,instruction_sha256=hashlib.sha256(instruction.encode()).hexdigest(),
      sol_advisor_context='Fresh native expert thread per intervention; same task workspace, full original task/images and public evidence retained. No cross-task answer reuse.',
      budget_note='Native response boundaries can overshoot targets; all model spend counts. Native completed edits remain available for grading after overspend; unknown usage cannot qualify for savings.')
    emit('hybrid-result',result)
  finally:
   with contextlib.suppress(Exception):await environment.upload_dir(self.logs_dir,str(self.environment_logs_dir))
   shutil.rmtree(private)
