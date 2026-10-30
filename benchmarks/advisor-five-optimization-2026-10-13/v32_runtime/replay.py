"""Post-trial original grading of immutable checkpoints, with no model calls.

This class is deliberately separate from the solver. It accepts only a complete
snapshot from a finalized source trial and restores only the original task's
declared file outputs. Directory snapshots are not currently supported.
"""
import json
import hashlib
import shlex
import tomllib
from pathlib import Path

from harbor.agents.base import BaseAgent
from .snapshots import verify_snapshot

ROOT = Path(__file__).resolve().parent.parent


def replay_model_cost(agent_logs):
    """Explicit no-model receipt, never invented native token-usage events."""
    logs = Path(agent_logs)
    receipt_path = logs/'replay-receipt.json'
    if not receipt_path.exists():
        return None
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('kind') != 'post_trial_checkpoint_grading'
            or receipt.get('model_calls') != 0
            or receipt.get('is_new_solver_attempt') is not False
            or any((logs/'phases').glob('*/request.json'))):
        return None
    return 0.0


def validate_replay(snapshot_dir, task, instruction):
    if task not in json.loads((ROOT/'manifest.json').read_text())['tasks']:
        raise ValueError('Task is outside the declared benchmark cohort')
    folder = Path(snapshot_dir).resolve()
    jobs = (ROOT / 'raw/jobs').resolve()
    if not folder.is_relative_to(jobs) or folder.parent.name != 'artifact-snapshots':
        raise ValueError('Replay source must be a recorded task checkpoint')
    if folder.parent.parent.name != 'agent':
        raise ValueError('Invalid source trial layout')
    trial = folder.parents[2]
    result_path = trial / 'result.json'
    if not result_path.exists():
        raise ValueError('Source trial has not finalized')
    result = json.loads(result_path.read_text())
    if result.get('exception_info') is not None:
        raise ValueError('Source trial has an infrastructure exception')
    events = [json.loads(line) for line in (ROOT/'raw/launch-events.jsonl').read_text().splitlines()]
    final = [e for e in events if e.get('event') == 'harbor_end'
             and e.get('name') == trial.parent.name
             and isinstance(e.get('result'), str) and Path(e['result']).resolve() == result_path]
    if len(final) != 1 or final[0].get('returncode') != 0 or final[0].get('exception') is not None:
        raise ValueError('No unique successful source-trial terminal event')
    original_instruction = ROOT/'tasks'/task/'instruction.md'
    if instruction.strip() != original_instruction.read_text().strip():
        raise ValueError('Replay instruction differs from the original task')
    snapshot = json.loads((folder/'snapshot.json').read_text())
    if snapshot['provenance']['task'] != task or not snapshot['complete']:
        raise ValueError('Snapshot is incomplete or belongs to another task')
    issues = verify_snapshot(folder)
    if issues:
        raise ValueError('Snapshot integrity check failed: '+ '; '.join(issues))
    artifacts = tomllib.loads((ROOT/'tasks'/task/'task.toml').read_text()).get('artifacts', [])
    if any(path.endswith('/') for path in artifacts):
        raise ValueError('Directory replay is not implemented')
    if [row['source_path'] for row in snapshot['files']] != list(dict.fromkeys(artifacts)):
        raise ValueError('Snapshot outputs differ from original task metadata')
    return snapshot, trial


class CheckpointReplay(BaseAgent):
    @staticmethod
    def name():
        return 'checkpoint-grader-replay-no-model'

    def version(self):
        return '1'

    def __init__(self, *args, snapshot_dir, snapshot_manifest_sha256, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_manifest_sha256 = snapshot_manifest_sha256

    async def setup(self, environment):
        pass

    async def run(self, instruction, environment, context):
        task = environment.environment_dir.parent.name
        manifest_bytes = (self.snapshot_dir/'snapshot.json').read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != self.snapshot_manifest_sha256:
            raise ValueError('Snapshot manifest changed after replay preparation')
        snapshot, source_trial = validate_replay(self.snapshot_dir, task, instruction)
        for row in snapshot['files']:
            parent = str(Path(row['source_path']).parent)
            command = 'mkdir -p -- ' + shlex.quote(parent)
            prepared = await environment.exec(command=command)
            if prepared.return_code != 0:
                raise RuntimeError('Could not prepare original artifact directory')
            await environment.upload_file(self.snapshot_dir/row['local_file'], row['source_path'])
        if (verify_snapshot(self.snapshot_dir)
                or hashlib.sha256((self.snapshot_dir/'snapshot.json').read_bytes()).hexdigest()
                != self.snapshot_manifest_sha256):
            raise ValueError('Source snapshot changed during upload')
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        receipt = {
            'kind': 'post_trial_checkpoint_grading',
            'source_trial': str(source_trial),
            'source_snapshot': str(self.snapshot_dir),
            'snapshot_manifest_sha256': self.snapshot_manifest_sha256,
            'provenance': snapshot['provenance'],
            'files': snapshot['files'],
            'model_calls': 0,
            'original_grader_changed': False,
            'scoring_feedback_available_to_source_solver': False,
            'is_new_solver_attempt': False,
        }
        (self.logs_dir/'replay-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
        # Explicit execution evidence; no fabricated model request or usage event.
        phase = self.logs_dir/'phases/executor-replay'
        phase.mkdir(parents=True)
        (phase/'execution-result.json').write_text(json.dumps({
            'status': 'completed', 'no_model_call': True, 'usage': {'cost_usd': 0.0},
            'execution': {'hidden_grader_used': False, 'artifact_restore_only': True},
        }, indent=2)+'\n')
        context.cost_usd = 0.0
        context.n_input_tokens = context.n_cache_tokens = context.n_output_tokens = 0
        context.metadata = receipt
