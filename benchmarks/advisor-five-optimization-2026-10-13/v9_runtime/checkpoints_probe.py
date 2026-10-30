"""Native Grok High custom-tool protocol fixture; no benchmark or shell execution."""
import asyncio,json,os,secrets
from pathlib import Path
from types import SimpleNamespace
from .sdk import advisor_turn

async def main():
    os.environ['CURSOR_API_KEY']=json.loads(Path(os.environ["CURSOR_AUTH_JSON"]).read_text())['apiKey']
    root=Path(__file__).resolve().parent.parent/'raw/preflight/cursor-remote-tools-checkpoints'
    root.mkdir(parents=True,exist_ok=False)
    value=secrets.token_hex(12);commands=[]
    async def fixture(command):
        assert command==["printf 'fixture-first'","printf 'fixture-second'"][len(commands)],'Unexpected fixture command'
        commands.append(command)
        return SimpleNamespace(return_code=0,stdout=value if len(commands)==1 else 'second-observation',stderr='')
    result=await advisor_turn(root/'phases/advisor-0',
        "Protocol fixture only, no real shell. In THIS turn call remote_command exactly once with command printf 'fixture-first', remember stdout, then immediately return JSON status working. In the NEXT turn call it exactly once with command printf 'fixture-second', then return JSON status completed with summary containing the exact random value from the first turn and the second stdout. Do not make both calls in one turn. No other action.",
        'high',.15,fixture)
    passed=result['status']=='completed' and len(commands)==2 and value in result['evidence']['text'] and 'second-observation' in result['evidence']['text'] and result['usage']['cost_usd'] is not None
    native=json.loads((root/'phases/advisor-0/sdk-result.json').read_text());passed=passed and len(native['turns'])==2
    report={'passed':passed,'benchmark_result':False,'remote_execution':'simulated callback, no shell','model':'grok-4.7','effort':'high',
        'native_turns':len(native['turns']),'calls':len(commands),'cost_usd':result['usage']['cost_usd'],'usage':result['usage']}
    (root/'probe.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='usage'}));assert passed

if __name__=='__main__':asyncio.run(main())
