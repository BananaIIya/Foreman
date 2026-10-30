"""Frozen v38 trial launcher. Every attempt unique; no automatic score retries."""
import argparse,hashlib,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
PYTHON='/private/tmp/kimi-k28-uv-tools/harbor/bin/python'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);ap.add_argument('--after-job');ap.add_argument('--dry-run',action='store_true');args=ap.parse_args()
 if args.after_job:
  print(json.dumps({'event':'waiting_finalized_dependency','name':args.after_job}),flush=True)
  while True:
   rows=[json.loads(x) for x in (ROOT/'raw/launch-events.jsonl').read_text().splitlines()]
   ends=[r for r in rows if r.get('event')=='harbor_end' and r.get('name')==args.after_job]
   if ends:
    assert len(ends)==1 and not ends[0].get('returncode') and not ends[0].get('exception');break
   time.sleep(15)
 manifest=json.loads((ROOT/'raw/candidates/v38/manifest.json').read_text())
 for path,digest in manifest['source_sha256'].items():
  assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest,'Frozen source drift: '+path
 if not args.dry_run:
  with (ROOT/'raw'/(args.config+'-v38-runtime.json')).open('x') as f:json.dump(manifest,f,indent=2)
 cmd=[PYTHON,str(ROOT/'launch.py'),'--config',args.config]
 if args.dry_run:cmd.append('--dry-run')
 subprocess.run(cmd,cwd=ROOT,check=True)
if __name__=='__main__':main()
