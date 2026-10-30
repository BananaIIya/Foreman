"""Synthetic no-container, no-benchmark-answer reviewer integration probe."""
import asyncio,json,shutil,tempfile
from pathlib import Path
from shared_workspace import make_home
from .reviewer import ReviewerSession
from .policy import DECISION, DECISION_SCHEMA
from hybrid_agent import parse_json
import jsonschema

ROOT=Path(__file__).resolve().parent.parent
async def main():
    trial=ROOT/'raw/jobs/transport-probe-v24-reviewer/fixture'
    trial.mkdir(parents=True,exist_ok=False)
    (trial/'config.json').write_text('{}\n')
    private=Path(tempfile.mkdtemp(prefix='reviewer-v24-',dir='/private/tmp'))
    home=make_home(private,'decision','gpt-6-luna','medium')
    work=private/'empty';work.mkdir()
    client=ReviewerSession(home,trial/'agent/phases/decision-probe','gpt-6-luna','medium')
    try:
        await client.start()
        payload={'stage':'delivery','original_task':'Produce /app/output.json from original data, validating all requested invariants.',
                 'worker_evidence':{'text':'The native expert stopped at a cost checkpoint. No final output or substantive invariant checks have been observed. Source files remain in /app; their contents are not present in this review payload.'},
                 'remaining_cost_target_usd':.08}
        result=await client.turn(None,json.dumps(payload),DECISION,str(work),schema=DECISION_SCHEMA,budget_usd=.01,no_tools=True)
        decision=parse_json(result['evidence']['text']);jsonschema.validate(decision,DECISION_SCHEMA)
        assert decision['action']=='advisor',decision['action']
        receipt={'status':'passed','model':'gpt-6-luna','effort':'medium','action':decision['action'],
                 'cost_usd':result['usage']['cost_usd'],'exception_info':None,'synthetic':True}
        (trial/'result.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps(receipt))
    finally:
        await client.close();shutil.rmtree(private)

if __name__=='__main__':asyncio.run(main())
