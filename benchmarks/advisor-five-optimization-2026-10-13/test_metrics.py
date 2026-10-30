import json
import tempfile
import unittest
from pathlib import Path
from metrics import aggregate_phases


class MixedUsageTest(unittest.TestCase):
    def test_both_providers_and_all_roles_reach_harbor_totals(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = [
                ('worker-discovery', 'grok-4.7-low', {'cost_usd':.018,
                    'native_usage':{'inputTokens':4000,'cacheReadTokens':8000,'cacheWriteTokens':0,'outputTokens':1000}}),
                ('decision-0', 'gpt-6-luna', {'cost_usd':.001,
                    'per_response_usage':[{'inputTokens':9000,'cachedInputTokens':8000,'cacheWriteInputTokens':0,'outputTokens':500}]}),
                ('advisor-0', 'gpt-6.1-sol', {'cost_usd':.025,
                    'per_response_usage':[{'inputTokens':13000,'cachedInputTokens':11000,'cacheWriteInputTokens':0,'outputTokens':2000}]}),
            ]
            for name, model, usage in rows:
                p = root/name; p.mkdir()
                (p/'request.json').write_text(json.dumps({'model':model}))
                (p/'phase-result.json').write_text(json.dumps({'usage':usage}))
            result = aggregate_phases(root)
            self.assertTrue(result['complete'])
            self.assertAlmostEqual(result['cost_usd'], .044)
            self.assertEqual(result['models']['grok-4.7-low']['n_input_tokens'], 12000)
            self.assertEqual(result['models']['gpt-6.1-sol']['n_input_tokens'], 13000)
            self.assertEqual(set(result['role_cost_usd']), {'worker','decision','advisor'})

    def test_missing_phase_cannot_disappear_from_total(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'advisor-0'; p.mkdir()
            (p/'request.json').write_text(json.dumps({'model':'gpt-6.1-sol'}))
            result = aggregate_phases(Path(folder))
            self.assertFalse(result['complete'])
            self.assertIsNone(result['cost_usd'])


if __name__ == '__main__':
    unittest.main()
