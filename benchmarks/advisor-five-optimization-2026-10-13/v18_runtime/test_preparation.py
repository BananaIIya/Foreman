import unittest
from .coordinator import Coordinator
from .policy import INITIAL_WORK


class WorkOrderRegression(unittest.IsolatedAsyncioTestCase):
    async def test_missing_observation_kind_keeps_command_opportunity_and_cost(self):
        calls = []
        async def worker(phase, payload, policy, cap):
            calls.append((phase, payload, policy, cap))
            return {'status': 'needs_advisor', 'usage': {'cost_usd': .012},
                    'evidence': {'text': 'Observed file schema; specialist question remains',
                                 'commands': [{'exit_code': 0, 'output': 'observed fields'}]}}
        async def unused(*args):
            raise AssertionError('No specialist or grader in this regression')
        c = Coordinator(.3, 'cursor/grok-4.7-low', worker, unused, unused, lambda *args: None, unused, unused)
        payload = {'work_order': {'work_kind': 'report_missing_evidence',
                                 'objective': 'Read accessible fixture fields'}}
        await c.work('worker-discovery', payload, 'obsolete caller policy', .06)
        self.assertEqual(calls[0][2], INITIAL_WORK)
        self.assertNotIn('report without inventing commands', calls[0][2])
        self.assertIn('maximum of two focused commands', calls[0][2])
        self.assertNotIn('original_task', calls[0][1])
        self.assertAlmostEqual(c.records[0]['cost_usd'], .012)
        await c.work('worker-implementation-1', {}, 'existing integration policy', .02)
        self.assertEqual(calls[1][2], 'existing integration policy')
        self.assertAlmostEqual(c.budget.spent, .024)


if __name__ == '__main__':
    unittest.main()
