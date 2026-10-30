"""One native inference probe of tool dispatch; no benchmark and no worker model."""
import asyncio
import argparse
import json
import shutil
import tempfile
from pathlib import Path
from shared_workspace import make_home
from hybrid_native import read_events,account_codex
from .native import AdvisorSession

ROOT=Path(__file__).resolve().parent.parent

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--name',default='v51-native-in-turn-tool');args=parser.parse_args()
    if not args.name.startswith('v51-native-in-turn-tool') or '/' in args.name:raise ValueError('Invalid fixture name')
    folder=ROOT/'raw/preflight'/args.name
    folder.mkdir(parents=True,exist_ok=False)
    private=Path(tempfile.mkdtemp(prefix='native-worker-tool-probe-',dir='/private/tmp'))
    client=AdvisorSession(make_home(private,'expert','gpt-6.1-sol','high'),folder,'gpt-6.1-sol','high')
    client.routine_worker_limit=.01;client.routine_worker_reserve=.005
    calls=[]
    async def fixture(work,cap):
        calls.append(work)
        return {'status':'completed','model_calls':0,'usage':{'cost_usd':0.},
                'evidence':{'text':'Synthetic tool fixture received the bounded work order; protocol marker DELEGATION_OK.',
                            'fixture':True,'remote_commands_executed':0}}
    client.routine_worker=fixture
    outcome={'scope':'synthetic native tool transport only','worker_model_calls':0,
             'benchmark_runs':0,'correctness_or_savings_claim':False}
    try:
        await client.start()
        result=await client.turn(None,
          'This is a synthetic protocol test, not a filesystem task. Call routine_worker once with procedure="Map the fixture integer to text", input_paths=["/app/fixture.json"], output_paths=["/app/result.txt"], resolved_assumptions="The synthetic fixture defines the integer", acceptance_checks=["Confirm the fixture tool receipt"]. These paths are virtual fixture identifiers; do not run shell commands or inspect files. After receiving the tool response, finish by returning its marker.',
          'Test the registered client tool. Use routine_worker exactly once. No shell, web, file access, other tools, or other models. Do not request approval.',
          str(private),budget_usd=.05)
        outcome.update(status=result['status'],native_usage=result['usage'],
                       tool_handler_calls=len(calls),marker_returned='DELEGATION_OK' in result['evidence']['text'])
    except Exception as exc:
        outcome.update(status='failed',error_type=type(exc).__name__,error=str(exc)[:1000],
                       native_usage=account_codex(read_events(folder/'app-server.events.jsonl'),'gpt-6.1-sol'))
    finally:
        await client.close();shutil.rmtree(private)
    (folder/'probe.json').write_text(json.dumps(outcome,indent=2)+'\n')
    print(json.dumps({k:v for k,v in outcome.items() if k!='native_usage'}))
    print(json.dumps({'actual_cost_usd':outcome['native_usage']['cost_usd']}))

if __name__=='__main__':asyncio.run(main())
