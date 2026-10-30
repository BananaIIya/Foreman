"""Real native Luna worker resume and per-turn accounting, no benchmark puzzle."""
import contextlib,json,shutil,tempfile
from pathlib import Path
from remote_native_codex import RemoteNativeCodex
from hybrid_native import Session
from shared_workspace import make_home,executor
from metrics import aggregate_phases

class LunaResumeProbe(RemoteNativeCodex):
 async def install(self,environment):
  await super().install(environment)
  await self.exec_as_root(environment,command='mkdir -p /probe')
 async def run(self,instruction,environment,context):
  logs=self.logs_dir/'phases';logs.mkdir(parents=True,exist_ok=True)
  private=Path(tempfile.mkdtemp(prefix='advisor-luna-resume-probe-',dir='/private/tmp'))
  try:
   async with executor(self,environment,private) as url:
    home=make_home(private,'worker','gpt-6-luna','medium')
    policy='You are testing native session continuity, not solving a benchmark. Work only in /probe using the bound remote executor. Follow each turn instruction; do not delegate or use external services.'
    client=Session(home,logs/'worker-discovery','gpt-6-luna','medium')
    try:
     await client.start()
     first=await client.turn(url,'Use one shell command to write /probe/resume.json with {"count":1,"owner":"luna"}. Then finish.',policy,'/probe')
    finally:await client.close()
    assert first['status']=='completed'
    client=Session(home,logs/'worker-implementation-0','gpt-6-luna','medium')
    try:
     await client.start()
     second=await client.turn(url,'Resume the file operation from the preceding turn. Read the file you wrote there, add 2 to its current count, preserve its owner field, and write it back. Read it again and report the actual saved count. Work only in /probe and finish after one shell command.',policy,'/probe',thread_id=first['thread_id'])
    finally:await client.close()
    assert second['status']=='completed' and second['thread_id']==first['thread_id']
    assert second['turn_id']!=first['turn_id']
    check=await environment.exec(command="python3 -c 'import json; from pathlib import Path; d=json.loads(Path(\"/probe/resume.json\").read_text()); assert d=={\"count\":3,\"owner\":\"luna\"}; Path(\"/probe/verified.txt\").write_text(\"handoff-ok\"); print(\"resume-verified\")'")
    assert check.return_code==0
    totals=aggregate_phases(logs)
    assert totals['complete'] and totals['cost_usd']>0
    (self.logs_dir/'transport-probe.json').write_text(json.dumps({'status':'passed','scope':'Luna native worker resume, not target benchmark','same_thread':True,'different_turn_ids':True,'verification_stdout':check.stdout,'usage':totals},indent=2)+'\n')
  finally:
   with contextlib.suppress(Exception):await environment.upload_dir(self.logs_dir,str(self.environment_logs_dir))
   shutil.rmtree(private)
 def populate_context_post_run(self,context):
  totals=aggregate_phases(self.logs_dir/'phases');context.cost_usd=totals['cost_usd'];context.metadata={'scope':'native worker resume preflight','role_cost_usd':totals['role_cost_usd']}
