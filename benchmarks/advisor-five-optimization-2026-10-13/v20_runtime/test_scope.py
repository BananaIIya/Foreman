import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from v11_runtime.test_worker import answer
from .coordinator import Coordinator
from .policy import preparation_images
from .worker import bounded_worker


class ScopedPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_repair_batch_also_skips_paid_report_call(self):
        calls = []
        async def native(*args):
            calls.append(True)
            return answer(commands=['repair fixture'])
        async def execute(command):
            return SimpleNamespace(return_code=0, stdout='repair observation', stderr='')
        with tempfile.TemporaryDirectory() as folder:
            result = await bounded_worker(Path(folder)/'worker-preparation-0', 'Scoped repair', 'low', .03, execute, native)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'preparation_observed')
        self.assertEqual(result['evidence']['commands'][0]['stdout'], 'repair observation')
        self.assertEqual(result['usage']['cost_usd'], .01)

    async def run_fixture(self, first_cost):
        calls, gates, experts = [], [], []
        order = {'work_kind': 'input_schema_check', 'objective': 'Observe fixture fields'}
        async def planner(*args):
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'work_order': order}
        async def worker(phase, payload, policy, cap):
            calls.append((phase, payload, cap))
            return {'status': 'preparation_observed', 'usage': {'cost_usd': first_cost if len(calls)==1 else .012},
                    'evidence': {'text': '{}', 'commands': [{'exit_code': 9, 'stderr': 'SyntaxError fixture'}]}}
        async def decision(phase, payload):
            gates.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'decision': {
                'action': 'deliver' if experts else 'continue', 'issue': 'Unresolved mechanical syntax error',
                'evidence': 'Observed execution failed', 'paths': ['/app/input'],
                'intervention': 'Fix the observed error', 'acceptance': 'Original requirements checked'}}
        async def advisor(phase, payload):
            experts.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': .04}, 'evidence': {'text': '{}'}}
        async def finalizer(*args):
            return None
        c = Coordinator(.3, 'cursor/grok-4.7-low', worker, decision, advisor, lambda *a: None, planner, finalizer)
        result = await c.run('FULL ORIGINAL SPECIALIST TASK')
        return result, calls, gates, experts

    async def test_repair_stays_scoped_and_cannot_repeat_indefinitely(self):
        result, calls, gates, experts = await self.run_fixture(.025)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1][0].startswith('worker-preparation-'))
        self.assertNotIn('original_task', calls[1][1])
        self.assertEqual(calls[1][1]['work_order']['objective'], 'Observe fixture fields')
        self.assertEqual(calls[1][1]['actual_previous_execution']['commands'][0]['exit_code'], 9)
        self.assertAlmostEqual(calls[1][2], .035)
        self.assertEqual(len(experts), 1)
        self.assertEqual(experts[0]['original_task'], 'FULL ORIGINAL SPECIALIST TASK')
        self.assertEqual(result['preparations'], 1)
        self.assertAlmostEqual(result['cost_usd'], .081)

    async def test_overspent_initial_call_does_not_get_a_fresh_worker_allowance(self):
        result, calls, _, experts = await self.run_fixture(.07)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(experts), 1)
        self.assertAlmostEqual(result['cost_usd'], .113)

    def test_preparation_repair_does_not_receive_specialist_images(self):
        images = [{'pixels': 'fixture'}]
        self.assertEqual(preparation_images('worker-preparation-0', images), [])
        self.assertIs(preparation_images('worker-implementation-2', images), images)


if __name__ == '__main__':
    unittest.main()
