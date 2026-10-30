import json
import tempfile
import unittest
from pathlib import Path
from trial_validity_v2 import planned_startup_failure


class PlannedStartupValidityTests(unittest.TestCase):
    def fixture(self, folder, text='', commands=0, extra_phase=False):
        base = Path(folder) / 'agent/phases'
        for name in ['decision-plan', 'worker-discovery'] + (['advisor-0'] if extra_phase else []):
            p = base / name
            p.mkdir(parents=True)
            (p / 'request.json').write_text(json.dumps({'no_tools': name=='decision-plan'}))
        (base / 'decision-plan/phase-result.json').write_text(json.dumps({
            'usage': {'cost_usd': .001}, 'evidence': {'command_count': 0}}))
        p = base / 'worker-discovery/rounds/0'
        p.mkdir(parents=True)
        (p / 'phase-result.json').write_text(json.dumps({'status': 'error',
            'native_error': {'code': 'connection_stalled'}, 'usage': {'cost_usd': None},
            'evidence': {'text': text, 'command_count': commands}}))
    def test_empty_provider_error_after_planning_is_invalid(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder)
            result = planned_startup_failure(folder)
            self.assertTrue(result['worker_cost_unknown_is_not_zero'])
            self.assertEqual(result['planning_cost_usd'], .001)
    def test_recovered_answer_is_not_classified_as_empty_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder, text='A recovered public answer')
            self.assertIsNone(planned_startup_failure(folder))
    def test_executed_commands_are_not_classified_as_empty_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder, commands=1)
            self.assertIsNone(planned_startup_failure(folder))
    def test_later_expert_phase_is_not_empty_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder, extra_phase=True)
            self.assertIsNone(planned_startup_failure(folder))


if __name__ == '__main__':
    unittest.main()
