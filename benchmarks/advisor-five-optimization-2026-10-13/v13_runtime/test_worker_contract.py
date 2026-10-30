import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from .worker_contract import WorkerRelay, worker_request


class NativeWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_bridge_keeps_actual_failures_and_full_usage(self):
        from .worker import worker_turn
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            usage = dict(inputTokens=100, outputTokens=100, cacheReadTokens=10, cacheWriteTokens=0)
            raw = {'status': 'finished', 'result': json.dumps({'status': 'working', 'summary': 'Need expert after observed failure', 'relevant_paths': []}),
                   'model': {'id': 'grok-4.7'}, 'usage': usage, 'usageEvents': [usage],
                   'turns': [{}], 'boundary': 'cost_boundary', 'known_cost_lower_bound_usd': .000805}
            sent = []
            executed = []
            class Input:
                def write(self, value):
                    sent.append(json.loads(value))
                async def drain(self):
                    pass
            process = SimpleNamespace(returncode=0, stdin=Input(),
                stdout=SimpleNamespace(readline=AsyncMock(side_effect=[
                    b'{"type":"remote_command","id":"0","command":"first"}\n',
                    b'{"type":"remote_command","id":"1","command":"second"}\n', b''])),
                wait=AsyncMock(return_value=0))
            async def spawn(*args, **kwargs):
                (folder / 'sdk-result.json').write_text(json.dumps(raw))
                return process
            async def execute(command):
                executed.append(command)
                return SimpleNamespace(return_code=1 if command=='second' else 0,
                    stdout='observed', stderr='actual failure' if command=='second' else '')
            with patch('v13_runtime.worker.asyncio.create_subprocess_exec', side_effect=spawn):
                result = await worker_turn(folder, 'synthetic original task', 'low', .0001, execute, [])
            self.assertEqual(executed, ['first', 'second'])
            self.assertEqual(sent[1]['result']['exit_code'], 1)
            self.assertEqual(result['evidence']['commands'][1]['stderr'], 'actual failure')
            self.assertAlmostEqual(result['usage']['cost_usd'], .000805)
            self.assertGreater(result['usage']['cost_usd'], .0001)
            self.assertEqual(result['interrupted'], 'cost_boundary')
            self.assertEqual(json.loads((folder/'phase-result.json').read_text()), result)
    async def test_task_execution_failure_is_returned_without_retry(self):
        calls = []
        async def execute(command):
            calls.append(command)
            return SimpleNamespace(return_code=1, stdout='partial result', stderr='dependency unavailable')
        relay = WorkerRelay(execute)
        result = await relay.handle({'command': 'task command'})
        self.assertEqual(calls, ['task command'])
        self.assertEqual(result['exit_code'], 1)
        self.assertEqual(result['stderr'], 'dependency unavailable')
    async def test_only_two_commands_reach_remote_executor(self):
        calls = []
        async def execute(command):
            calls.append(command)
            return SimpleNamespace(return_code=0, stdout='ok', stderr='')
        relay = WorkerRelay(execute)
        for command in ['first', 'second', 'third']:
            result = await relay.handle({'command': command})
        self.assertEqual(calls, ['first', 'second'])
        self.assertIn('controller_error', result)
    async def test_oversized_and_empty_commands_consume_attempt_without_execution(self):
        calls = []
        async def execute(command):
            calls.append(command)
        relay = WorkerRelay(execute)
        for command in ['x' * 6001, '']:
            result = await relay.handle({'command': command})
            self.assertTrue(result['rejected'])
            self.assertFalse(result['started'])
        self.assertEqual(len(relay.commands), 2)
        self.assertEqual(calls, [])
    def test_request_retains_model_effort_images_and_cost_budget(self):
        images = [{'data': 'synthetic', 'mimeType': 'image/png'}]
        req = worker_request('original task', 'low', .06, images)
        self.assertEqual(req['model'], 'grok-4.7')
        self.assertEqual(req['effort'], 'low')
        self.assertEqual(req['images'], images)
        self.assertEqual(req['budget_usd'], .06)
        self.assertEqual(req['native_turn_limit'], 1)
        self.assertEqual(req['command_limit'], 2)
        self.assertEqual(req['tools'], ['mcp'])
        self.assertEqual(req['setting_sources'], [])
        self.assertEqual(req['mcp_servers'], {})


if __name__ == '__main__':
    unittest.main()
