"""Serial frozen V8 Sol-advisor cohort; no score retries or WAL/ATRX reruns."""
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PYTHON='/private/tmp/kimi-k28-uv-tools/harbor/bin/python'
previous=None
for task in ['hof-topology-interpenetration','satb-audio-transcription','cad-model']:
    name='hybrid-v8-'+task
    command=[PYTHON,'-m','v8_runtime.launch','--config',name]
    if previous:command+=['--after-job',previous]
    subprocess.run(command,cwd=ROOT,check=True)
    previous=name
