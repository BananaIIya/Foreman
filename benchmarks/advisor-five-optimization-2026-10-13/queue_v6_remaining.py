"""Two remaining Sol-advisor tasks, after the complete CAD trial frees capacity.

WAL and ATRX are intentionally absent: their extra attempts await approval.
No task is retried on a wrong answer; all three use the same frozen candidate.
"""
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PYTHON='/private/tmp/kimi-k28-uv-tools/harbor/bin/python'
previous='hybrid-v6-cad-model'
for task in ['hof-topology-interpenetration','satb-audio-transcription']:
    name='hybrid-v6-'+task
    subprocess.run([PYTHON,'-m','v6_runtime.launch','--after-job',previous,'--config',name],cwd=ROOT,check=True)
    previous=name
