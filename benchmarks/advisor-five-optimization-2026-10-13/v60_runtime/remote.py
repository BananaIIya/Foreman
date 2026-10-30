"""Remote task-command wrapper; isolated interpreter (-I) so workspace files cannot shadow stdlib modules."""
import inspect,json,shlex
from types import SimpleNamespace
from v9_runtime.representation import compact_json, bounded_text

async def run_task_command(agent, environment, command):
    # The only subprocess runs inside the task's Daytona container.
    script = 'import json\n'+inspect.getsource(compact_json)+'\n'+inspect.getsource(bounded_text)+'\n'+'''import json,os,signal,subprocess
command=json.loads(COMMAND_LITERAL)
p=subprocess.Popen(command,shell=True,executable='/bin/bash',cwd='/app',stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',start_new_session=True)
deadline=False
try:out,err=p.communicate(timeout=300)
except subprocess.TimeoutExpired:
 deadline=True
 os.killpg(p.pid,signal.SIGTERM)
 try:out,err=p.communicate(timeout=5)
 except subprocess.TimeoutExpired:
  os.killpg(p.pid,signal.SIGKILL);out,err=p.communicate()
if deadline:err+='\\nController command deadline exceeded (300 seconds); process group stopped.'
stdout=bounded_text(out,48000);stderr=bounded_text(err,6000)
print(json.dumps({'return_code':124 if deadline else p.returncode,'stdout':stdout.pop('text'),'stderr':stderr.pop('text'),'stdout_truncated':stdout['truncated'],'stderr_truncated':stderr['truncated'],'representation':{'stdout':stdout,'stderr':stderr},'deadline_exceeded':deadline}))
'''.replace('COMMAND_LITERAL',repr(json.dumps(command)))
    output=await agent.exec_as_agent(environment,command='python3 -I -c '+shlex.quote(script),cwd='/app',timeout_sec=330)
    if output.return_code:
        raise RuntimeError('Remote execution wrapper failed; not a model command exit')
    row=json.loads(output.stdout)
    return SimpleNamespace(**row)
