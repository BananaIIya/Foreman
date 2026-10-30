"""Correct only the frozen auditor's raw-file versus Harbor instruction comparison.

The candidate and frozen auditor are unchanged. Harbor strips leading canary
comments before calling any agent; compare to that installed loader output.
"""
import hashlib
import inspect
import json
from pathlib import Path
from harbor.models.task.task import Task
from audit_v46 import audit, ROOT


def supplement():
    base=audit()
    checks=[];verified=set()
    tasks=json.loads((ROOT/'manifest.json').read_text())['tasks']
    for task in tasks:
        for trial in (ROOT/'raw/jobs'/('hybrid-v46-'+task)).glob('*/result.json'):
            agent=trial.parent/'agent';h=json.loads((agent/'hybrid-result.json').read_text())
            expected=Task(ROOT/'tasks'/task).instruction
            matched=h['instruction_sha256']==hashlib.sha256(expected.encode()).hexdigest()
            requests=[]
            for request in (agent/'phases').glob('advisor-*/request.json'):
                actual=json.loads(json.loads(request.read_text())['prompt'])['original_task']
                requests.append({'phase':request.parent.name,'matches_harbor_instruction':actual==expected})
            checks.append({'task':task,'coordinator_instruction_matches_harbor':matched,'advisor_requests':requests})
            if matched and all(r['matches_harbor_instruction'] for r in requests):verified.add(task)
    allowed={task+': '+label for task in verified for label in ['Original instruction changed','Advisor original task changed']}
    remaining=[p for p in base['problems'] if p not in allowed]
    source=Path(inspect.getfile(Task))
    result={'candidate':'v46','frozen_auditor_findings':base['problems'],'correction':'Compare against Harbor Task.instruction, which removes leading canary marker comments; do not change original task files or runtime.',
            'installed_loader':str(source),'installed_loader_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'checks':checks,'remaining_problems':remaining,'source_files_checked':base['source_files_checked'],
            'original_files_checked':base['original_files_checked'],
            'goal_evidence_passes':not remaining and base['acceptance']['numerical_acceptance_pass']}
    (ROOT/'audit-v46-instruction-supplement.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':print(json.dumps(supplement()))
