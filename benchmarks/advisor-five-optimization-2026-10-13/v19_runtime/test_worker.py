import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from v11_runtime.test_worker import answer
from .worker import bounded_worker


class BatchHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def simulate(self, replies, *, name='worker-discovery', fail=False):
        sequence = iter(replies)
        calls, commands = [], []
        async def native(folder, prompt, effort):
            calls.append(prompt)
            return next(sequence)
        async def execute(command):
            commands.append(command)
            return SimpleNamespace(return_code=9 if fail else 0, stdout='actual file observation', stderr='actual failure' if fail else '')
        with tempfile.TemporaryDirectory() as folder:
            result = await bounded_worker(Path(folder)/name, 'Synthetic scoped preparation', 'low', .2, execute, native)
        return result, calls, commands

    async def test_one_command_returns_actual_output_without_paid_report(self):
        result, calls, commands = await self.simulate([answer(commands=['inspect'])])
        self.assertEqual(commands, ['inspect'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'preparation_observed')
        self.assertEqual(result['usage']['cost_usd'], .01)
        evidence = json.loads(result['evidence']['text'])
        self.assertTrue(evidence['report_is_before_execution'])
        self.assertEqual(evidence['actual_command_results'][0]['stdout'], 'actual file observation')
        self.assertEqual(evidence['controller_boundaries']['call_limit'], 1)

    async def test_failure_preserved_and_rest_of_batch_not_run(self):
        result, calls, commands = await self.simulate([answer(commands=['fails', 'must-not-run'])], fail=True)
        self.assertEqual(commands, ['fails'])
        self.assertEqual(len(calls), 1)
        evidence = json.loads(result['evidence']['text'])
        self.assertEqual(evidence['actual_command_results'][0]['exit_code'], 9)
        self.assertEqual(evidence['actual_command_results'][0]['stderr'], 'actual failure')
        self.assertEqual(evidence['protocol_history'][0]['unexecuted_commands'], ['must-not-run'])
        self.assertNotEqual(result['status'], 'completed')

    async def test_missing_usage_remains_unknown(self):
        result, calls, _ = await self.simulate([answer(commands=['inspect'], cost=None)])
        self.assertEqual(len(calls), 1)
        self.assertIsNone(result['usage']['cost_usd'])
        self.assertIsNone(result['usage']['native_usage'])

    async def test_later_worker_can_respond_to_execution_errors(self):
        result, calls, commands = await self.simulate(
            [answer(commands=['fails']), answer('needs_advisor')], name='worker-implementation-1', fail=True)
        self.assertEqual(commands, ['fails'])
        self.assertEqual(len(calls), 2)
        self.assertIn('actual failure', calls[1])
        self.assertEqual(result['usage']['cost_usd'], .02)

    async def test_invalid_protocol_never_executes_or_retries_initial_paid_call(self):
        reply = answer(commands=['do-not-run'])
        reply['patch'] = None
        result, calls, commands = await self.simulate([reply])
        self.assertEqual(len(calls), 1)
        self.assertFalse(commands)
        self.assertEqual(result['status'], 'invalid_worker_protocol')
        self.assertEqual(result['usage']['cost_usd'], .01)


if __name__ == '__main__':
    unittest.main()
