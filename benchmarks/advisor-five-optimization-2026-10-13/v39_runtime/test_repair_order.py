import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from v33_runtime.test_focus import FocusTests
from .coordinator import Coordinator
from .native import AdvisorSession


class RepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_margin_and_correction_reserve(self):
        with patch('v33_runtime.test_focus.Coordinator', Coordinator):
            result, calls, gates, emits = await FocusTests().run_fixture()
        self.assertAlmostEqual(result['target_ceiling_usd'], .36)
        self.assertAlmostEqual(calls[0]['cost_target_usd'], (.36-.022-.005)*.70)
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(result['cost_usd'], .104)
        self.assertFalse(result['verifier_feedback'])

    async def test_warning_steers_once_without_new_model_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            client = object.__new__(AdvisorSession)
            client.log_dir = Path(folder)
            calls = []
            async def rpc(method, params):
                calls.append((method, params))
                return {'ok': True}
            client.rpc = rpc
            await client.budget_notice('thread', 'turn', .075, .1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], 'turn/steer')
            self.assertEqual(calls[0][1]['expectedTurnId'], 'turn')
            receipt = json.loads((client.log_dir/'budget-notice.json').read_text())
            self.assertTrue(receipt['hard_stop_unchanged'])
            self.assertEqual(receipt['threshold'], .75)
            self.assertEqual(receipt['attempts'], 1)
            self.assertIn('do not guess', receipt['message'])

    async def test_finished_turn_does_not_trigger_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            client = object.__new__(AdvisorSession)
            client.log_dir = Path(folder)
            calls = []
            async def rpc(method, params):
                calls.append(method)
                raise RuntimeError('turn ended')
            client.rpc = rpc
            await client.budget_notice('thread', 'turn', .075, .1)
            self.assertEqual(calls, ['turn/steer'])
            self.assertEqual(json.loads((client.log_dir/'budget-notice.json').read_text())['status'], 'not_confirmed')
