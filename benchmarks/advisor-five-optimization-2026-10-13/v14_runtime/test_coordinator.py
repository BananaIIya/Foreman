import json
import unittest
import jsonschema
from .coordinator import Coordinator
from .policy import PLAN_SCHEMA


ORDER = {'objective': 'Count rows and save a schema check', 'input_paths': ['/app/input.csv'],
         'requirement_excerpt': 'Input has a header and numeric columns',
         'expected_evidence': 'Actual row count and schema check exit code',
         'specialist_boundary': 'Do not infer the scientific classification'}


class PlannedWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_and_original_task_are_separated_without_losing_expert_context(self):
        worker_inputs, gates, experts, ledger = [], [], [], []
        original = 'Original full scientific assignment plus required artifact'
        async def planner(phase, payload, cap):
            self.assertEqual(phase, 'decision-plan')
            self.assertEqual(payload['original_task'], original)
            self.assertGreater(cap, 0)
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'work_order': ORDER}
        async def worker(phase, payload, policy, cap):
            worker_inputs.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': .015},
                    'evidence': {'text': json.dumps({'status': 'completed', 'summary': 'Only row count checked'}),
                                 'commands': [{'exit_code': 0, 'stdout': 'observed rows'}]}}
        async def decision(phase, payload):
            gates.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'decision': {
                'action': 'advisor' if len(gates)==1 else 'deliver', 'issue': 'Specialist classification remains',
                'evidence': 'Only routine schema checked', 'paths': ['/app/input.csv'],
                'intervention': 'Complete classification from original data', 'acceptance': 'Original requirement checked'}}
        async def advisor(phase, payload):
            experts.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': .1},
                    'evidence': {'text': json.dumps({'status': 'completed'})}}
        def emit(name, value):
            if name == 'ledger':
                ledger[:] = value
        result = await Coordinator(.3, 'cursor/grok-4.7-low', worker, decision, advisor, emit, planner).run(original)
        self.assertNotIn('original_task', worker_inputs[0])
        self.assertEqual(worker_inputs[0]['work_order'], ORDER)
        self.assertEqual(gates[0]['stage'], 'discovery')
        self.assertEqual(gates[0]['original_task'], original)
        self.assertEqual(experts[0]['original_task'], original)
        self.assertEqual(experts[0]['worker_evidence'], gates[0]['worker_evidence'])
        self.assertAlmostEqual(result['cost_usd'], .118)
        self.assertEqual(ledger[0]['phase'], 'decision-plan')
        self.assertEqual(ledger[0]['role'], 'decision')
        self.assertEqual(result['terminal_reason'], 'deliver')
    async def test_unknown_planning_usage_prevents_additional_paid_calls(self):
        called = []
        async def planner(*args):
            return {'status': 'failed', 'usage': {'cost_usd': None}, 'work_order': ORDER}
        async def forbidden(*args):
            called.append(True)
        c = Coordinator(.3, 'cursor/grok-4.7-low', forbidden, forbidden, forbidden, lambda *args: None, planner)
        with self.assertRaises(RuntimeError):
            await c.run('synthetic assignment')
        self.assertEqual(called, [])
        self.assertFalse(c.budget.usage_complete)
    def test_plan_schema_is_bounded_and_has_no_command_or_answer_field(self):
        jsonschema.validate(ORDER, PLAN_SCHEMA)
        for modified in [{**ORDER, 'command': 'unexpected'}, {**ORDER, 'objective': 'x'*801}]:
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(modified, PLAN_SCHEMA)


if __name__ == '__main__':
    unittest.main()
