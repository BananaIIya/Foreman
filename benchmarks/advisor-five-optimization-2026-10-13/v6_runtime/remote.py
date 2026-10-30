"""Remote task-command deadline is observable exit124, not a transport failure."""
import json,shlex
from types import SimpleNamespace

async def run_task_command(agent, environment, command):
    # The only subprocess runs inside the task's Daytona container.
    script = '''import json,os,signal,subprocess
command=json.loads(COMMAND_LITERAL)
p=subprocess.Popen(command,shell=True,executable='/bin/bash',cwd='/app',stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
deadline=False
try:out,err=p.communicate(timeout=300)
except subprocess.TimeoutExpired:
 deadline=True
 os.killpg(p.pid,signal.SIGTERM)
 try:out,err=p.communicate(timeout=5)
 except subprocess.TimeoutExpired:
  os.killpg(p.pid,signal.SIGKILL);out,err=p.communicate()
if deadline:err+='\\nController command deadline exceeded (300 seconds); process group stopped.'
print(json.dumps({'return_code':124 if deadline else p.returncode,'stdout':out[:18000],'stderr':err[:6000],'stdout_truncated':len(out)>18000,'stderr_truncated':len(err)>6000,'deadline_exceeded':deadline}))
'''.replace('COMMAND_LITERAL',repr(json.dumps(command)))
    output=await agent.exec_as_agent(environment,command='python -c '+shlex.quote(script),cwd='/app',timeout_sec=330)
    if output.return_code:
        raise RuntimeError('Remote execution wrapper failed; not a model command exit')
    row=json.loads(output.stdout)
    if row['stdout_truncated']:row['stdout']+='\n[controller truncated stdout]'
    if row['stderr_truncated']:row['stderr']+='\n[controller truncated stderr]'
    return SimpleNamespace(**row)
