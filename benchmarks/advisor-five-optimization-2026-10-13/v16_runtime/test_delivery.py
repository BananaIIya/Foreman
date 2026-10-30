import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from .delivery import execute_delivery, validate_manifest
from .coordinator import Coordinator


MANIFEST = {'version': 1, 'token': 'current-intervention', 'commands': ['invoke implemented generator'],
            'check_commands': ['check original requirement'], 'artifact_paths': ['/app/result.dat']}


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_manifest_bound_to_current_intervention_and_bounded_commands(self):
        self.assertEqual(validate_manifest(MANIFEST, 'current-intervention'), MANIFEST)
        invalid = [dict(MANIFEST, token='old-intervention'), dict(MANIFEST, commands=[]),
                   dict(MANIFEST, commands=['x']*5), dict(MANIFEST, check_commands=[]),
                   dict(MANIFEST, artifact_paths=['/app/../outside']), dict(MANIFEST, version=True),
                   dict(MANIFEST, extra='not allowed')]
        for value in invalid:
            with self.assertRaises(ValueError):
                validate_manifest(value, 'current-intervention')
    async def test_literal_order_no_model_and_no_automatic_replay(self):
        commands = []
        async def run(command):
            commands.append(command)
            return SimpleNamespace(return_code=0, stdout='actual output', stderr='')
        with tempfile.TemporaryDirectory() as folder:
            result = await execute_delivery(folder, MANIFEST, 'current-intervention', run, {'text': 'Expert implementation and assumptions'})
            self.assertEqual(commands, MANIFEST['commands'] + MANIFEST['check_commands'])
            self.assertTrue(result['no_model_call'])
            self.assertEqual(result['usage']['cost_usd'], 0)
            self.assertFalse(result['execution']['hidden_grader_used'])
            self.assertTrue(result['execution']['all_commands_succeeded'])
            self.assertEqual(json.loads((Path(folder)/'execution-result.json').read_text()), result)
            with self.assertRaises(FileExistsError):
                await execute_delivery(folder, MANIFEST, 'current-intervention', run, {})
            self.assertEqual(len(commands), 2)
    async def test_failed_delivery_stops_before_check_and_retains_error(self):
        async def run(command):
            return SimpleNamespace(return_code=7, stdout='partial', stderr='actual failure')
        with tempfile.TemporaryDirectory() as folder:
            result = await execute_delivery(folder, MANIFEST, 'current-intervention', run, {})
            self.assertFalse(result['execution']['all_commands_succeeded'])
            self.assertEqual(result['execution']['checks'][0]['return_code'], 7)
            self.assertEqual(result['execution']['checks'][0]['stderr'], 'actual failure')
            self.assertEqual(len(result['execution']['unexecuted_commands']), 1)
    async def test_overspent_advisor_can_finish_literal_delivery_without_new_inference(self):
        calls = []
        async def planner(*args):
            calls.append('planner')
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'work_order': {'objective': 'Routine setup'}}
        async def worker(*args):
            calls.append('worker')
            return {'status': 'completed', 'usage': {'cost_usd': .01}, 'evidence': {'text': '{}'}}
        async def decision(*args):
            calls.append('decision')
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'decision': {
                'action': 'advisor', 'issue': 'Unresolved original requirement', 'evidence': 'Observed gap',
                'paths': [], 'intervention': 'Implement specialist correction', 'acceptance': 'Original checks pass'}}
        async def advisor(*args):
            calls.append('advisor')
            return {'status': 'interrupted', 'usage': {'cost_usd': .25}, 'evidence': {'text': 'Implementation saved'}}
        with tempfile.TemporaryDirectory() as folder:
            async def run(command):
                calls.append('literal:' + command)
                return SimpleNamespace(return_code=0, stdout='done', stderr='')
            async def finalizer(phase, advice):
                return await execute_delivery(Path(folder)/phase, MANIFEST, 'current-intervention', run, advice['evidence'])
            c = Coordinator(.3, 'cursor/grok-4.7-low', worker, decision, advisor, lambda *args: None, planner, finalizer)
            result = await c.run('Synthetic full assignment')
        self.assertEqual(calls[:4], ['planner', 'worker', 'decision', 'advisor'])
        self.assertEqual(len(calls), 6)
        self.assertAlmostEqual(result['cost_usd'], .262)
        self.assertEqual(result['execution_only_integrations'], 1)
        self.assertEqual(result['terminal_reason'], 'budget_exhausted_after_integration')
        self.assertEqual(result['phases'][-1]['role'], 'executor')
        self.assertEqual(result['phases'][-1]['cost_usd'], 0)


if __name__ == '__main__':
    unittest.main()
