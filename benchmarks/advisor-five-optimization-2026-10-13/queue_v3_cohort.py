"""Three authorized unstarted tasks after audited v2 trials release capacity."""
import json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PYTHON='/private/tmp/kimi-k28-uv-tools/harbor/bin/python'
deps=['hybrid-v2-wal-recovery-ordering','hybrid-v2-atrx-vep-crispr']
print(json.dumps({'event':'waiting_for_finalized_jobs','dependencies':deps,'next_tasks':['cad-model','hof-topology-interpenetration','satb-audio-transcription'],'wal_atrx_reruns':False}),flush=True)
while True:
 rows=[]
 for line in (ROOT/'raw/launch-events.jsonl').read_text().splitlines():
  try:rows.append(json.loads(line))
  except ValueError:pass
 found={}
 for name in deps:
  ends=[r for r in rows if r.get('event')=='harbor_end' and r.get('name')==name]
  if not ends:continue
  assert len(ends)==1
  end=ends[0]
  if end.get('returncode') or end.get('exception'):
   exc=end.get('exception') or {}
   allowed=exc.get('exception_type')=='RuntimeError' and exc.get('exception_message')=='Cursor SDK failed; inspect phase console without exposing credentials'
   natives=list((ROOT/'raw/jobs'/name).glob('*/agent/phases/advisor-*/sdk-result.json'))
   allowed=allowed and any((json.loads(p.read_text()).get('error') or {}).get('code')=='connection_stalled' for p in natives)
   if not allowed:raise RuntimeError('Unaudited dependency error; inspect before proceeding')
   # This is never relabeled a correct/wrong model answer. Sol-advisor tasks
   # use another adapter; the known Grok SDK failure does not affect them.
  found[name]=end
 if len(found)==len(deps):break
 time.sleep(15)
for task in ['cad-model','hof-topology-interpenetration','satb-audio-transcription']:
 subprocess.run([PYTHON,'-m','v3_runtime.launch','--config','hybrid-v3-'+task],cwd=ROOT,check=True)
