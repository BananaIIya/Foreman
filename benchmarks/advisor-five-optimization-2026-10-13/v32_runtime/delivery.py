"""Task-scoped, explicitly repeatable expert delivery after each intervention."""
import json
from pathlib import Path
from v16_runtime.delivery import validate_manifest as validate_v1
from v8_runtime.execution import execute_commands


def validate_manifest(value, token):
    if not isinstance(value, dict) or value.get('scope') != 'task' or value.get('repeat_after_advisor') is not True:
        raise ValueError('Expert must explicitly authorize repeatable task delivery')
    if type(value.get('version')) is not int or value['version'] != 2:
        raise ValueError('Expected task delivery protocol version 2')
    legacy = {k:v for k,v in value.items() if k not in ('scope','repeat_after_advisor')}
    legacy['version'] = 1
    validate_v1(legacy, token)
    return value


async def execute_delivery(folder, manifest, token, run_command, expert_evidence):
    validate_manifest(manifest, token)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    previous = []
    for path in sorted(folder.parent.glob('executor-*/delivery-started.json')):
        journal = json.loads(path.read_text())
        if journal.get('token') != token:
            continue
        result_path = path.with_name('execution-result.json')
        if not result_path.exists():
            raise RuntimeError('Prior delivery completion is unknown; do not replay')
        completed = json.loads(result_path.read_text())
        if completed.get('status') != 'completed' or completed.get('execution',{}).get('delivery_token') != token:
            raise RuntimeError('Prior delivery completion is not confirmed; do not replay')
        previous.append(path.parent.name)
    # Same intervention may never be executed twice, even after confirmed success.
    with (folder/'delivery-started.json').open('x') as f:
        json.dump({'token':token,'manifest':manifest,'scope':'task',
                   'previous_confirmed_executions':previous,
                   'no_automatic_replay_of_uncertain_execution':True},f)
    patch = {'status':'patch','setup_commands':manifest['commands'],
             'validation_commands':manifest['check_commands'],'files':[],
             'evidence_paths':manifest['artifact_paths'],'requested_evidence':[],
             'handoff':'Expert-authorized repeatable generation and checks after this advisor intervention.'}
    result = await execute_commands(patch, run_command)
    result['execution'].update(delivery_token=token,expert_evidence=expert_evidence,
                               delivery_scope='task',previous_confirmed_executions=previous)
    result['evidence']['text'] = json.dumps(result['execution'],ensure_ascii=False)
    (folder/'execution-result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
