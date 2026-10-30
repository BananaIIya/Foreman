import copy
import json
import tempfile
import unittest
from pathlib import Path
import jsonschema
from .coordinator import Coordinator
from .policy import DECISION_SCHEMA
from .reviewer import reviewed_decision


FOCUS = {'primary_uncertainty': 'Whether two input records were incorrectly merged',
         'alternative_explanations': 'Distinct source records versus duplicate measurements',
         'discriminating_observation': 'Compare independently observed source identifiers before aggregation',
         'repair_goal': 'Preserve each distinct record exactly once in the required output'}


class FocusTests(unittest.IsolatedAsyncioTestCase):
    async def run_fixture(self, focused=True, second_action='advisor'):
        calls, emits, gates = [], {}, []
        async def planner(*args):
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'work_order': {'objective': 'Input parser'}}
        async def worker(*args):
            return {'status': 'completed', 'usage': {'cost_usd': .02}, 'evidence': {'text': 'Parser executed'}}
        async def decision(phase, payload):
            gates.append(payload)
            action = 'advisor' if not calls else second_action if len(calls)==1 else 'deliver'
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'decision': {
                'action': action, 'issue': 'Unresolved record identity' if calls else 'Specialist implementation required',
                'evidence': 'Observed conflicting identifiers' if calls else 'Input parser exists; implementation remains',
                'paths': ['/app/output'], 'intervention': 'Complete the original output',
                'acceptance': 'All original input records accounted for',
                'priority_check': copy.deepcopy(FOCUS) if focused else None}}
        async def advisor(phase, payload):
            calls.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': .04},
                    'evidence': {'text': json.dumps({'status':'needs_advisor' if len(calls)==1 else 'completed'})}}
        async def finalizer(*args): return None
        c=Coordinator(.5,'cursor/grok-4.7-low',worker,decision,advisor,
                      lambda k,v: emits.update({k:v}),planner,finalizer)
        result=await c.run('Complete original deliverable, preserving ALL requirements')
        return result,calls,gates,emits

    async def test_only_followup_gets_priority_without_scope_or_cost_change(self):
        result,calls,gates,emits=await self.run_fixture()
        self.assertEqual(len(calls),2)
        self.assertEqual(len(gates),3)
        self.assertNotIn('corrective_focus',calls[0])
        self.assertEqual(calls[1]['corrective_focus'],FOCUS)
        self.assertEqual(calls[1]['contract']['priority_check'],FOCUS)
        self.assertEqual(calls[0]['original_task'],calls[1]['original_task'])
        self.assertEqual(result['terminal_reason'],'deliver')
        self.assertAlmostEqual(result['cost_usd'],.104)
        self.assertFalse(result['verifier_feedback'])
        self.assertEqual(emits['corrective-focus-advisor-1']['extra_model_calls'],0)

    async def test_missing_focus_is_not_fabricated(self):
        result,calls,_,emits=await self.run_fixture(focused=False)
        self.assertNotIn('corrective_focus',calls[1])
        self.assertEqual(emits['corrective-focus-advisor-1']['status'],'no_grounded_priority_available')
        self.assertEqual(result['terminal_reason'],'deliver')

    async def test_completion_override_preserves_grounded_priority(self):
        _,calls,_,emits=await self.run_fixture(second_action='deliver')
        self.assertEqual(calls[1]['corrective_focus'],FOCUS)
        self.assertIn('completion-review-decision-1',emits)

    async def test_native_tool_failure_has_no_invented_priority(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/'phase-result.json').write_text(json.dumps({'interrupted':'decision_model_attempted_tool','usage':{'cost_usd':.003}}))
            async def invoke():raise RuntimeError('decision_model_attempted_tool')
            r=await reviewed_decision(invoke,p)
            self.assertIsNone(r['decision']['priority_check'])
            self.assertEqual(r['usage']['cost_usd'],.003)
            jsonschema.validate(r['decision'],DECISION_SCHEMA)

    def test_schema_does_not_mutate_frozen_parent(self):
        from v32_runtime.policy import DECISION_SCHEMA as prior
        self.assertNotIn('priority_check',prior['properties'])
        focus_schema=DECISION_SCHEMA['properties']['priority_check']
        jsonschema.validate(FOCUS,focus_schema)
        jsonschema.validate(None,focus_schema)
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate({'primary_uncertainty':'Guess'},focus_schema)
