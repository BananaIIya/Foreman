import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
import jsonschema
from .coordinator import Coordinator, unsupported_requirement
from .policy import DECISION_SCHEMA
from .reviewer import reviewed_decision


def evidence(basis='original_input', gap=False):
    return [{'requirement': 'Preserve every distinct original record', 'basis': basis,
             'observation': 'Source identifiers independently compared with exported identifiers',
             'material_gap': gap}]


class CoverageTests(unittest.IsolatedAsyncioTestCase):
    async def run_fixture(self, coverage, expert_cost=.04):
        calls, gates, emits = [], [], {}
        async def planner(*args):
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'work_order': {}}
        async def worker(*args):
            return {'status': 'completed', 'usage': {'cost_usd': .02},
                    'evidence': {'text': 'Parser executes with original input'}}
        async def decision(phase, payload):
            gates.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'decision': {
                'action': 'advisor' if not calls else 'deliver', 'issue': 'Original task remains',
                'evidence': 'Observed records', 'paths': ['/app/output'],
                'intervention': 'Complete the original task', 'acceptance': 'All original records',
                'priority_check': None, 'completion_evidence': None if not calls else
                    copy.deepcopy(coverage if len(calls) == 1 else evidence())}}
        async def advisor(phase, payload):
            calls.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': expert_cost},
                    'evidence': {'text': '{"status":"completed"}'}}
        async def finalizer(*args): return None
        c = Coordinator(.5, 'cursor/grok-4.7-low', worker, decision, advisor,
                        lambda k,v: emits.update({k:v}), planner, finalizer)
        r = await c.run('Preserve ALL original records in the original required format')
        return r, calls, gates, emits

    async def test_admitted_gap_cannot_deliver_even_with_expert_completion(self):
        r, calls, gates, emits = await self.run_fixture(evidence(gap=True))
        self.assertEqual(len(calls), 2)
        self.assertEqual(r['terminal_reason'], 'deliver')
        self.assertAlmostEqual(r['cost_usd'], .104)
        self.assertEqual(calls[0]['original_task'], calls[1]['original_task'])
        self.assertEqual(calls[1]['contract']['completion_evidence'], evidence(gap=True))
        receipt = emits['evidence-coverage-review-decision-1']
        self.assertFalse(receipt['budget_reset'])
        self.assertFalse(receipt['correctness_claimed'])
        self.assertFalse(receipt['verifier_feedback'])
        self.assertNotIn('evidence-coverage-review-decision-2', emits)

    async def test_supported_delivery_does_not_buy_another_advisor(self):
        for basis in ('original_input', 'required_behavior', 'grounded_domain_reasoning'):
            with self.subTest(basis=basis):
                r, calls, _, emits = await self.run_fixture(evidence(basis))
                self.assertEqual(len(calls), 1)
                self.assertEqual(r['terminal_reason'], 'deliver')
                self.assertAlmostEqual(r['cost_usd'], .063)
                self.assertFalse(any(k.startswith('evidence-coverage-review') for k in emits))

    async def test_internal_consistency_and_missing_coverage_reopen_review(self):
        for coverage in (None, evidence('internal_consistency_only'), evidence('missing')):
            with self.subTest(coverage=coverage):
                r, calls, _, emits = await self.run_fixture(coverage)
                self.assertEqual(len(calls), 2)
                self.assertIn('evidence-coverage-review-decision-1', emits)

    async def test_gap_does_not_reset_or_extend_exhausted_budget(self):
        r, calls, _, _ = await self.run_fixture(evidence(gap=True), expert_cost=.34)
        self.assertEqual(len(calls), 1)
        self.assertEqual(r['terminal_reason'], 'budget_exhausted_after_integration')
        self.assertAlmostEqual(r['cost_usd'], .362)
        self.assertAlmostEqual(r['target_ceiling_usd'], .36)

    def test_later_gap_not_hidden_by_first_supported_requirement(self):
        rows = evidence() + evidence('missing')
        self.assertIsNotNone(unsupported_requirement({'completion_evidence': rows}))

    async def test_protocol_failure_retains_charge_without_invented_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            native = {'status': 'interrupted', 'interrupted': 'decision_model_attempted_tool',
                      'usage': {'cost_usd': .003}, 'evidence': {'text': ''}}
            (p/'phase-result.json').write_text(json.dumps(native))
            invoke = AsyncMock(side_effect=RuntimeError('decision_model_attempted_tool'))
            r = await reviewed_decision(invoke, p)
            jsonschema.validate(r['decision'], DECISION_SCHEMA)
            self.assertIsNone(r['decision']['completion_evidence'])
            self.assertEqual(r['decision']['action'], 'advisor')
            self.assertEqual(r['usage'], native['usage'])
            self.assertEqual(invoke.await_count, 1)

    def test_old_schema_immutable_and_new_schema_rejects_unstructured_claim(self):
        from v41_runtime.policy import DECISION_SCHEMA as prior
        self.assertNotIn('completion_evidence', prior['properties'])
        schema = DECISION_SCHEMA['properties']['completion_evidence']
        jsonschema.validate(evidence(), schema)
        jsonschema.validate(None, schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate('All checks passed', schema)


if __name__ == '__main__': unittest.main()
