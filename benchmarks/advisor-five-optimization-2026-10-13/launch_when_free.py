"""Queue a single trial after explicit owned dependencies are finalized."""
import argparse,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PYTHON='/private/tmp/kimi-k28-uv-tools/harbor/bin/python'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--after-job',action='append',required=True);ap.add_argument('--config',required=True);args=ap.parse_args()
 print(json.dumps({'event':'waiting_for_finalized_jobs','dependencies':args.after_job,'next_config':args.config}),flush=True)
 while True:
  rows=[]
  for line in (ROOT/'raw/launch-events.jsonl').read_text().splitlines():
   try:rows.append(json.loads(line))
   except ValueError:pass
  finalized={}
  for name in args.after_job:
   found=[r for r in rows if r.get('event')=='harbor_end' and r.get('name')==name]
   if len(found)>1:raise RuntimeError('Multiple dependency attempts; inspect manually')
   if not found:continue
   end=found[0]
   if end.get('returncode') or end.get('exception'):
    print(json.dumps({'event':'next_trial_not_started','reason':'dependency_infrastructure_error','dependency':name}),flush=True);return
   finalized[name]=end
  if len(finalized)==len(args.after_job):break
  time.sleep(15)
 subprocess.run([PYTHON,str(ROOT/'launch.py'),'--config',args.config],cwd=ROOT,check=True)
if __name__=='__main__':main()
