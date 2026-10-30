"""Queue one pilot after its baseline is finalized; never retry or alter a run."""
import argparse,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PYTHON='/private/tmp/kimi-k28-uv-tools/harbor/bin/python'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--baseline',required=True);ap.add_argument('--config',required=True);args=ap.parse_args()
 print(json.dumps({'event':'waiting_for_baseline','baseline':args.baseline,'candidate':args.config}),flush=True)
 while True:
  rows=[]
  for line in (ROOT/'raw/launch-events.jsonl').read_text().splitlines():
   try:rows.append(json.loads(line))
   except ValueError:pass
  ends=[r for r in rows if r.get('event')=='harbor_end' and r.get('name')==args.baseline]
  if ends:
   assert len(ends)==1;end=ends[0]
   if end.get('returncode') or end.get('exception') or (end.get('reward') or {}).get('rewards',{}).get('reward')!=1:
    print(json.dumps({'event':'pilot_not_started','reason':'baseline_not_valid_correct','baseline':args.baseline}),flush=True);return
   break
  time.sleep(15)
 subprocess.run([PYTHON,str(ROOT/'launch.py'),'--config',args.config],cwd=ROOT,check=True)
if __name__=='__main__':main()
