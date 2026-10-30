"""Paid native protocol preflight using a labeled fixture, never a task score."""
import asyncio,json,os,secrets
from pathlib import Path
from types import SimpleNamespace
from v6_runtime.worker import bounded_worker
ROOT=Path(__file__).resolve().parent.parent

async def main():
    folder=ROOT/'raw/preflight/cursor-bounded-worker'
    folder.mkdir(parents=True,exist_ok=False)
    os.environ['CURSOR_API_KEY']=json.loads(Path(os.environ["CURSOR_AUTH_JSON"]).read_text())['apiKey']
    value='native-protocol-'+secrets.token_hex(8)
    async def fixture(command):
        if command.strip()!='cat /app/probe.txt':
            return SimpleNamespace(return_code=1,stdout='',stderr='Fixture supports only cat /app/probe.txt; no shell execution occurred.')
        return SimpleNamespace(return_code=0,stdout=value,stderr='Simulated fixture output; no shell or benchmark execution.')
    result=await bounded_worker(folder/'phases/worker-discovery',
        'This is a native model protocol preflight with a simulated fixture callback, not a benchmark. First request exactly cat /app/probe.txt. After the controller returns its fixture output, return completed with that exact output in report.summary. Do not invent the unseen value.',
        'low',.06,fixture,execution_label='Simulated fixture callback; no shell/benchmark execution')
    passed=result['status']=='completed' and len(result['native_calls'])==2 and result['evidence']['command_count']==1 and value in json.loads(result['evidence']['text'])['summary'] and result['usage']['cost_usd'] is not None
    report={'passed':passed,'kind':'native model / controller protocol only; simulated command output, not benchmark evidence','cost_usd':result['usage']['cost_usd'],'native_calls':len(result['native_calls']),'native_statuses':[x['status'] for x in result['native_calls']]}
    (folder/'probe.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
    assert passed,'Native bounded worker protocol preflight failed'
if __name__=='__main__':asyncio.run(main())
