import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from v20_runtime.native import AdvisorSession as Parent
from .native import AdvisorSession


class DefaultRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_start_params_and_workflow_preserved(self):
        original = {'model': 'gpt-6.1-sol', 'developerInstructions': 'original workflow',
                    'config': {'model_reasoning_effort': 'high'},
                    'environments': [{'environmentId': 'daytona'}]}
        for method in ('thread/start', 'thread/resume'):
            with tempfile.TemporaryDirectory() as d:
                client = object.__new__(AdvisorSession)
                client.log_dir = Path(d)
                params = copy.deepcopy(original)
                with patch.object(Parent, 'rpc', new=AsyncMock(return_value={'ok': True})) as send:
                    await client.rpc(method, params)
                    send.assert_awaited_once_with(method, original)
                self.assertEqual(params, original)
                receipt = json.loads((client.log_dir/'specialist-base-policy.json').read_text())
                self.assertFalse(receipt['custom_base_instructions_supplied'])
                self.assertEqual(receipt['effort'], 'high')

    async def test_rejects_accidental_custom_role(self):
        client = object.__new__(AdvisorSession)
        with self.assertRaises(ValueError):
            await client.rpc('thread/start', {'baseInstructions': 'override'})

    def test_existing_cost_warning_implementation_preserved(self):
        from v39_runtime.native import AdvisorSession as Previous
        self.assertIs(AdvisorSession.budget_notice, Previous.budget_notice)
