"""Run the remaining frozen cohort after both 4-GiB trials finalize."""
import json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PYTHON='/private/tmp/kimi-k28-uv-tools/harbor/bin/python'
deps=['hybrid-v2-wal-recovery-ordering','hybrid-v2-atrx-vep-crispr']
print(json.dumps({'event':'waiting_for_finalized_jobs','dependencies':deps}),flush=True)
while True:
 rows=[]
 for line in (ROOT/'raw/launch-events.jsonl').read_text().splitlines():
  try:rows.append(json.loads(line))
  except ValueError:pass
 found={}
 for name in deps:
  ends=[r for r in rows if r.get('event')=='harbor_end' and r.get('name')==name]
  if ends:
   assert len(ends)==1
   if ends[0].get('returncode') or ends[0].get('exception'):
    raise RuntimeError('Infrastructure failure in dependency; inspect before continuing cohort')
   found[name]=ends[0]
 if len(found)==len(deps):break
 time.sleep(15)
for task in ['cad-model','hof-topology-interpenetration','satb-audio-transcription']:
 subprocess.run([PYTHON,'-m','v2_runtime.launch','--config','hybrid-v2-'+task],cwd=ROOT,check=True)
