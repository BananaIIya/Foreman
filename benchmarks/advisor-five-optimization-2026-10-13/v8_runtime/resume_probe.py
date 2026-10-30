"""Native Sol High context/accounting preflight; no benchmark or tools."""
import asyncio,json,secrets,shutil,tempfile
from pathlib import Path
from hybrid_native import Session
from shared_workspace import make_home
from hybrid_agent import parse_json
ROOT=Path(__file__).resolve().parent.parent

async def main():
    out=ROOT/'raw/preflight/sol-advisor-resume';out.mkdir(parents=True,exist_ok=False)
    private=Path(tempfile.mkdtemp(prefix='sol-resume-probe-',dir='/private/tmp'))
    home=make_home(private,'advisor','gpt-6.1-sol','high');work=private/'empty';work.mkdir()
    value=secrets.token_hex(12);thread=None;results=[]
    schemas=[{'type':'object','additionalProperties':False,'required':['ack'],'properties':{'ack':{'type':'boolean'}}},
        {'type':'object','additionalProperties':False,'required':['value'],'properties':{'value':{'type':'string'}}}]
    prompts=['Protocol preflight only. Remember this random fixture value for the next turn: '+value+'. Return ack true.',
        'Return the exact fixture value from the preceding turn in this same conversation. This new request intentionally omits it.']
    try:
        for index in range(2):
            client=Session(home,out/'phases'/('advisor-'+str(index)),'gpt-6.1-sol','high')
            try:
                await client.start()
                result=await client.turn(None,prompts[index],'Protocol preflight only. No tools. Return only the requested JSON.',str(work),thread_id=thread,schema=schemas[index],no_tools=True)
                results.append(result);thread=result['thread_id']
            finally:await client.close()
        passed=(all(r['status']=='completed' and r['usage']['cost_usd'] is not None for r in results)
            and results[0]['thread_id']==results[1]['thread_id'] and results[0]['turn_id']!=results[1]['turn_id']
            and parse_json(results[1]['evidence']['text']).get('value')==value)
        report={'passed':passed,'benchmark_result':False,'model':'gpt-6.1-sol','effort':'high',
            'same_task_context_resumed_across_native_processes':passed,
            'cost_usd':sum(r['usage']['cost_usd'] or 0 for r in results),
            'per_turn_usage':[r['usage'] for r in results]}
        (out/'probe.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k!='per_turn_usage'}))
        assert passed,'Native advisor context resumption failed'
    finally:shutil.rmtree(private)
if __name__=='__main__':asyncio.run(main())
