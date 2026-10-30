"""Serial frozen V9 Sol-advisor cohort; no score retries or WAL/ATRX reruns."""
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PYTHON='/private/tmp/kimi-k28-uv-tools/harbor/bin/python'
previous=None
for task in ['cad-model','hof-topology-interpenetration','satb-audio-transcription']:
    name='hybrid-v9-'+task
    command=[PYTHON,'-m','v9_runtime.launch','--config',name]
    if previous:command+=['--after-job',previous]
    subprocess.run(command,cwd=ROOT,check=True)
    previous=name
