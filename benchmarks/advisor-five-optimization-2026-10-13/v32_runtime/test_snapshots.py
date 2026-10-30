import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from harbor.models.agent.context import AgentContext
from v9_runtime.metrics import aggregate_phases

from .snapshots import capture_snapshot, verify_snapshot
from . import replay


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_binary_versions_are_distinct_and_do_not_modify_source(self):
        remote = {'/app/output.bin': b'first\x00\xffbinary'}
        calls = []
        async def download(source, target):
            calls.append(source)
            target.write_bytes(remote[source])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = await capture_snapshot(root/'advisor-0', ['/app/output.bin'], download, {'advisor_phase':'advisor-0'})
            remote['/app/output.bin'] = b'second\x00binary'
            second = await capture_snapshot(root/'advisor-1', ['/app/output.bin'], download, {'advisor_phase':'advisor-1'})
            self.assertEqual((root/'advisor-0'/first['files'][0]['local_file']).read_bytes(), b'first\x00\xffbinary')
            self.assertEqual((root/'advisor-1'/second['files'][0]['local_file']).read_bytes(), remote['/app/output.bin'])
            self.assertNotEqual(first['files'][0]['sha256'], second['files'][0]['sha256'])
            self.assertEqual(verify_snapshot(root/'advisor-0'), [])
            self.assertFalse(first['exposed_to_models'])
            self.assertEqual(first['model_calls'] + first['grader_calls'], 0)
            self.assertEqual(calls, ['/app/output.bin', '/app/output.bin'])

    async def test_duplicate_paths_read_once_and_missing_bytes_remain_unknown(self):
        calls = []
        async def download(source, target):
            calls.append(source)
            if source.endswith('missing'):
                target.write_bytes(b'partial')
                raise OSError('Synthetic read failure')
            target.write_bytes(b'observed')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'phase'
            result = await capture_snapshot(folder, ['/app/ok','/app/ok','/app/missing'], download, {})
            self.assertEqual(calls, ['/app/ok','/app/missing'])
            self.assertFalse(result['complete'])
            self.assertEqual(result['files'][1]['status'], 'unavailable')
            self.assertFalse(list(folder.glob('*.partial')))
            self.assertEqual(verify_snapshot(folder), [])

    async def test_invalid_paths_and_directories_never_download(self):
        async def download(*args):
            self.fail('Invalid path must not reach remote download')
        with tempfile.TemporaryDirectory() as tmp:
            result = await capture_snapshot(Path(tmp)/'phase', ['/etc/passwd','/app/../secret','/app/.codex/auth.json','/app/'], download, {})
            self.assertFalse(result['complete'])
            self.assertTrue(all(r['status'] in ('rejected_path','unsupported_directory') for r in result['files']))

    async def test_corruption_detected_and_prior_checkpoint_never_overwritten(self):
        async def download(source, target):
            target.write_bytes(b'original')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'phase'
            record = await capture_snapshot(folder, ['/app/out'], download, {})
            with self.assertRaises(FileExistsError):
                await capture_snapshot(folder, ['/app/out'], download, {})
            (folder/record['files'][0]['local_file']).write_bytes(b'changed!')
            self.assertTrue(verify_snapshot(folder))


class ReplayEligibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_finalized_original_task_and_unchanged_complete_snapshot_can_replay(self):
        async def download(source, target):
            target.write_bytes(b'original model output')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root/'tasks/fixture'
            (root/'manifest.json').write_text('{"tasks":["fixture"]}')
            task.mkdir(parents=True)
            (task/'instruction.md').write_text('Original task')
            (task/'task.toml').write_text('artifacts = ["/app/out"]\n')
            trial = root/'raw/jobs/hybrid-fixture/fixture__unique'
            snapshot = trial/'agent/artifact-snapshots/advisor-0'
            await capture_snapshot(snapshot, ['/app/out'], download, {'task':'fixture','advisor_phase':'advisor-0'})
            with patch.object(replay, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'not finalized'):
                    replay.validate_replay(snapshot, 'fixture', 'Original task')
                (trial/'result.json').write_text('{"exception_info":null}')
                events = root/'raw/launch-events.jsonl'
                events.write_text('')
                with self.assertRaisesRegex(ValueError, 'terminal event'):
                    replay.validate_replay(snapshot, 'fixture', 'Original task')
                events.write_text(json.dumps({'event':'harbor_end','name':'hybrid-fixture','result':str(trial/'result.json'),'returncode':0,'exception':None})+'\n')
                record, original = replay.validate_replay(snapshot, 'fixture', 'Original task')
                self.assertEqual(original, trial.resolve())
                self.assertTrue(record['complete'])
                uploaded = {}
                commands = []
                async def upload(source, destination):
                    uploaded[destination] = Path(source).read_bytes()
                async def execute(command):
                    commands.append(command)
                    return SimpleNamespace(return_code=0)
                environment=SimpleNamespace(environment_dir=task/'environment', upload_file=upload, exec=execute)
                agent=replay.CheckpointReplay(logs_dir=root/'replay-logs',snapshot_dir=snapshot,
                    snapshot_manifest_sha256=hashlib.sha256((snapshot/'snapshot.json').read_bytes()).hexdigest())
                context=AgentContext()
                await agent.run('Original task',environment,context)
                self.assertEqual(uploaded,{'/app/out':b'original model output'})
                self.assertEqual(commands,['mkdir -p -- /app'])
                self.assertEqual(context.cost_usd,0)
                self.assertFalse(context.metadata['scoring_feedback_available_to_source_solver'])
                self.assertIsNone(aggregate_phases(root/'replay-logs/phases')['cost_usd'])
                self.assertEqual(replay.replay_model_cost(root/'replay-logs'),0)
                with self.assertRaisesRegex(ValueError, 'instruction differs'):
                    replay.validate_replay(snapshot, 'fixture', 'Changed task')
                (snapshot/record['files'][0]['local_file']).write_bytes(b'tampered output')
                with self.assertRaisesRegex(ValueError, 'integrity'):
                    replay.validate_replay(snapshot, 'fixture', 'Original task')

    def test_solver_prompts_and_routing_policy_are_unchanged(self):
        from v31_runtime import policy as prior
        from . import policy as current
        for field in ['PLAN','PLAN_SCHEMA','INITIAL_WORK','WORKER_SYSTEM','DECISION','DECISION_SCHEMA','ADVISOR','SOL_ADVISOR']:
            self.assertEqual(getattr(prior, field), getattr(current, field))


if __name__ == '__main__':
    unittest.main()
