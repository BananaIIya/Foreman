"""Read-only source, execution-boundary, accounting and original-grade audit."""
import hashlib
import json
import math
from pathlib import Path
from harbor.models.task.task import Task
from acceptance_report import evaluate
from trial_validity_v2 import result_for
from v9_runtime.metrics import aggregate_phases
from v51_runtime.handoff import validate_manifest
from v32_runtime.snapshots import verify_snapshot

ROOT=Path(__file__).resolve().parent


def audit():
    frozen=json.loads((ROOT/'raw/candidates/v51/manifest.json').read_text())
    original=json.loads((ROOT/'manifest.json').read_text())
    problems=[]; rows=[]
    for path,digest in frozen['source_sha256'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:problems.append('Frozen source changed: '+path)
    original_paths={p:h for p,h in original['sources_sha256'].items() if p.startswith('tasks/')}
    for path,digest in original_paths.items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:problems.append('Original task changed: '+path)
    for task in original['tasks']:
        name='hybrid-v51-'+task
        row={'task':task, 'result':result_for(name), 'problems':[]}
        rows.append(row)
        trials=list((ROOT/'raw/jobs'/name).glob('*/result.json'))
        if not trials:continue
        def fail(message):row['problems'].append(message)
        if len(trials)!=1:fail('Multiple attempts in candidate');continue
        guard=ROOT/'raw'/(name+'-v51-runtime.json')
        if not guard.exists() or json.loads(guard.read_text())!=frozen:fail('Launch source guard mismatch')
        agent=trials[0].parent/'agent'
        if not (agent/'hybrid-result.json').exists():fail('Missing coordinator completion');continue
        h=json.loads((agent/'hybrid-result.json').read_text())
        if h['assignment']!=frozen['assignment'][task]:fail('Model assignment changed')
        if h['advisor_calls']>2 or h['integrations']>1:fail('Intervention limits exceeded')
        if h['verifier_feedback'] is not False or h['advisor_workspace_access'] is not True:fail('Execution boundary changed')
        totals=aggregate_phases(agent/'phases')
        if not totals['complete'] or not h['usage_complete']:fail('Incomplete native usage')
        if h['cost_usd'] is None or totals['cost_usd'] is None or not math.isclose(h['cost_usd'],totals['cost_usd'],abs_tol=1e-10):fail('Native fee ledger mismatch')
        if h['cost_usd'] is not None and not math.isclose(sum(x['cost_usd'] or 0 for x in h['phases']),h['cost_usd'],abs_tol=1e-10):fail('Phase sum mismatch')
        if not math.isclose(h['target_ceiling_usd'],h['baseline_cost_usd']*.72):fail('Ceiling changed')
        instruction=Task(ROOT/'tasks'/task).instruction
        if h['instruction_sha256']!=hashlib.sha256(instruction.encode()).hexdigest():fail('Original instruction changed')
        expert_threads=[]
        for record in h['phases']:
            if record['role']=='executor':continue
            folder=agent/'phases'/record['phase']
            request=json.loads((folder/'request.json').read_text())
            if record['role']=='advisor' and request.get('live_review_threshold')!=.65:fail('Source review timing drift')
            if record['role']=='advisor' and h['assignment']['advisor']=='gpt-6.1-sol':
                payload=json.loads(request['prompt'])
                if payload['original_task']!=instruction:fail('Advisor original task changed')
                if payload['late_handoff_contract']['phase']!=record['phase']:fail('Wrong handoff phase')
                expert_threads.append(request['thread_id'])
            if record['role']=='worker' and h['assignment']['worker'].startswith('cursor/'):
                if request['native_call_limit']!=1 or request['command_limit']!=2 or request['tools']!=[]:fail('Cursor worker bounds changed')
                if 'Bash -e -o pipefail' not in request['execution_environment']:fail('Strict execution absent')
                native=json.loads((folder/'phase-result.json').read_text())
                if len(native['native_calls'])>1 or native['evidence']['command_count']>2:fail('Actual Cursor bounds exceeded')
        for entry in [x for x in h['phases'] if x['role']=='advisor']:
            folder=agent/'phases'/entry['phase']
            native=json.loads((folder/'phase-result.json').read_text())
            review=native.get('live_review')
            if review is None:continue
            name='decision-live-'+entry['phase']
            records=[x for x in h['phases'] if x['phase']==name]
            if len(records)!=1 or records[0]['role']!='decision':fail('Live review not charged exactly once')
            review_folder=agent/'phases'/name
            recorded=json.loads((review_folder/'phase-result.json').read_text())
            if recorded!=review:fail('Live review result differs from separately charged phase')
            request=json.loads((review_folder/'request.json').read_text())
            payload=json.loads(request['prompt'])
            if request['model']!='gpt-6-luna' or request['effort']!='medium' or request['no_tools'] is not True:fail('Live reviewer model or tool boundary changed')
            if payload['original_task']!=instruction or payload['hidden_grader_available'] is not False:fail('Live reviewer task or grading boundary changed')
            if payload.get('workspace_excerpts')!=json.loads((review_folder/'live-artifact-excerpts.json').read_text()):fail('Live artifact evidence differs from actual excerpts')
            evidence=json.loads((folder/'live-review-evidence.json').read_text())
            if payload['current_public_evidence']!=evidence:fail('Live reviewer did not receive the recorded public evidence snapshot')
            receipt=json.loads((folder/'live-review-receipt.json').read_text())
            if receipt['attempts']!=1 or receipt['grader_feedback'] is not False or receipt['review_cost_separate_from_native_usage'] is not True:fail('Live review boundary drift')
            if receipt['review_cost_usd']!=review['usage']['cost_usd']:fail('Live review fee mismatch')
            if h['assignment']['advisor']=='gpt-6.1-sol':
                if receipt['same_thread']!=native['thread_id'] or receipt['same_turn']!=native['turn_id']:fail('Live feedback changed expert session')
            if review.get('review') is not None:
                import jsonschema
                from v51_runtime.live_review import SCHEMA
                try:jsonschema.validate(review['review'],SCHEMA)
                except jsonschema.ValidationError:fail('Invalid accepted live review report')
        if len(expert_threads)!=len(set(expert_threads)):fail('Expert corrective thread reused')
        for entry in [x for x in h['phases'] if x['role']=='advisor']:
            folder=agent/'phases'/entry['phase']
            native=json.loads((folder/'phase-result.json').read_text())
            request=json.loads((folder/'request.json').read_text())
            if request.get('in_turn_worker_enabled') is not True:fail('In-turn worker unavailable')
            if request.get('budget_includes_in_turn_worker') is not True:fail('Worker budget separated incorrectly')
            receipt=json.loads((folder/'in-turn-worker-receipt.json').read_text())
            if receipt['grader_feedback'] is not False or receipt['native_usage_excludes_worker'] is not True:fail('Worker evidence/cost boundary drift')
            if len(receipt['records'])>1:fail('More than one worker tool call per expert')
            if h['assignment']['advisor']=='gpt-6.1-sol':
                if receipt['same_thread']!=native['thread_id'] or receipt['same_turn']!=native['turn_id']:fail('Worker callback changed native expert session')
            charged=0.
            for item in receipt['records']:
                worker=item['result']
                if not worker.get('model_calls',1):continue
                name=worker['phase']
                entries=[x for x in h['phases'] if x['phase']==name]
                if len(entries)!=1 or entries[0]['role']!='worker':fail('Delegated worker not charged exactly once')
                actual=json.loads((agent/'phases'/name/'phase-result.json').read_text())
                if actual!=worker:fail('Worker receipt differs from actual phase result')
                if worker['usage']['cost_usd'] is None:fail('Unknown delegated worker cost')
                else:charged+=worker['usage']['cost_usd']
                if worker['evidence'].get('command_count',0)>2:fail('Delegated worker command bound exceeded')
                inputs=json.loads((agent/('in-turn-worker-inputs-'+entry['phase']+'.json')).read_text())
                if inputs['original_task']!=instruction or inputs['hidden_grader_available'] is not False:fail('Delegated worker task boundary changed')
                for index,file in enumerate(inputs['actual_input_files']):
                    snapshot=agent/'in-turn-worker-input-snapshots'/entry['phase']/(str(index)+'.input')
                    if hashlib.sha256(snapshot.read_bytes()).hexdigest()!=file['sha256']:fail('Delegated worker input snapshot changed')
            if receipt['cost_usd'] is None or not math.isclose(receipt['cost_usd'],charged,abs_tol=1e-10):fail('Delegated worker total mismatch')
        for folder in agent.glob('late-handoff-*'):
            if not folder.is_dir() or not (folder/'receipt.json').exists():continue
            receipt=json.loads((folder/'receipt.json').read_text())
            if receipt['correctness_proven'] is not False or receipt['model_calls']!=0:fail('Handoff receipt misrepresents evidence')
            if receipt['status']!='available':continue
            raw=(folder/'manifest.json').read_bytes()
            if hashlib.sha256(raw).hexdigest()!=receipt['manifest_sha256']:fail('Handoff manifest changed')
            try:validate_manifest(json.loads(raw),receipt['contract'])
            except Exception:fail('Invalid accepted handoff schema')
            for i,item in enumerate(receipt['manifest']['inputs']):
                if hashlib.sha256((folder/('input-'+str(i))).read_bytes()).hexdigest()!=item['sha256']:fail('Handoff input changed')
        for entry in h['phases']:
            if not entry['phase'].startswith('worker-integration-'):continue
            receipts=list(agent.glob('late-handoff-*-revalidated/receipt.json'))
            if not any(json.loads(x.read_text())['status']=='available' for x in receipts):fail('Worker integration lacks revalidated concrete handoff')
        snapshot=agent/'artifact-snapshots/final'
        if not (snapshot/'snapshot.json').exists():fail('Final snapshot missing')
        else:row['problems'].extend(verify_snapshot(snapshot))
        problems.extend(task+': '+x for x in row['problems'])
    acceptance=evaluate('v51')
    out={'candidate':'v51','problems':problems,'rows':rows,'source_files_checked':len(frozen['source_sha256']),
         'original_files_checked':len(original_paths),'acceptance':acceptance,
         'goal_evidence_passes':not problems and acceptance['numerical_acceptance_pass'],
         'limitations':['One sample does not prove repeat-run stability.','File hash integrity is not semantic correctness.','Native token price equivalents exclude infrastructure and outer engineering.']}
    (ROOT/'audit-v51.json').write_text(json.dumps(out,indent=2)+'\n')
    return out


if __name__=='__main__':
    r=audit();print(json.dumps({k:r[k] for k in ['candidate','problems','source_files_checked','original_files_checked','goal_evidence_passes']}))
