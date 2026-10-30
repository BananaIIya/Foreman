"""Native Grok High custom-tool protocol fixture; no benchmark or shell execution."""
import asyncio,json,os,secrets
from pathlib import Path
from types import SimpleNamespace
from .sdk import advisor_turn

async def main():
    os.environ['CURSOR_API_KEY']=json.loads(Path(os.environ["CURSOR_AUTH_JSON"]).read_text())['apiKey']
    root=Path(__file__).resolve().parent.parent/'raw/preflight/cursor-remote-tools'
    root.mkdir(parents=True,exist_ok=False)
    value=secrets.token_hex(12);commands=[]
    async def fixture(command):
        assert command=="printf 'fixture-only'",'Unexpected fixture command'
        commands.append(command)
        return SimpleNamespace(return_code=0,stdout=value,stderr='')
    result=await advisor_turn(root/'phases/advisor-0',
        "Protocol fixture only. Discover the remote_command custom tool and call it exactly once with command printf 'fixture-only'. The tool executes a simulated callback, not a shell. Return the exact value it reports in stdout. No other action.",
        'high',.10,fixture)
    passed=result['status']=='completed' and len(commands)==1 and value in result['evidence']['text'] and result['usage']['cost_usd'] is not None
    report={'passed':passed,'benchmark_result':False,'remote_execution':'simulated callback, no shell','model':'grok-4.7','effort':'high',
        'calls':len(commands),'cost_usd':result['usage']['cost_usd'],'usage':result['usage']}
    (root/'probe.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='usage'}));assert passed

if __name__=='__main__':asyncio.run(main())
