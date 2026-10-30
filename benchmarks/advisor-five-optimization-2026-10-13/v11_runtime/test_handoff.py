import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from v10_runtime.coordinator import Coordinator
from v11_runtime.worker import bounded_worker


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_exhausted_worker_passes_real_outputs_directly_to_gate_and_expert(self):
        calls = []
        gates = []
        expert_inputs = []

        async def native(folder, prompt, effort):
            calls.append(prompt)
            self.assertEqual(len(calls), 1, 'No paid worker report call after its command budget is exhausted')
            return {'status': 'completed', 'usage': {'cost_usd': .01, 'native_usage': {
                'inputTokens': 100, 'outputTokens': 20, 'cacheReadTokens': 0, 'cacheWriteTokens': 0}},
                'patch': {'status': 'commands', 'commands': ['make draft', 'check draft'],
                          'report': {'summary': 'Proposed draft, not yet verified', 'relevant_paths': ['/app/draft.py']}},
                'evidence': {'text': 'proposed commands'}}

        async def command(cmd):
            return SimpleNamespace(return_code=7 if cmd == 'check draft' else 0,
                                   stdout='observed failed invariant' if cmd == 'check draft' else 'draft saved', stderr='')

        async def decision(phase, payload):
            gates.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'evidence': {'text': 'gate'},
                    'decision': {'action': 'advisor' if len(gates) == 1 else 'deliver',
                                 'issue': 'Observed invariant needs specialist repair',
                                 'evidence': 'Actual failed command', 'paths': ['/app/draft.py'],
                                 'intervention': 'Repair the failed invariant', 'acceptance': 'Check original requirement'}}

        async def advisor(phase, payload):
            expert_inputs.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': .08},
                    'evidence': {'text': json.dumps({'status': 'completed', 'checks': ['repaired original invariant']})}}

        with tempfile.TemporaryDirectory() as directory:
            async def worker(phase, payload, policy, cap):
                return await bounded_worker(Path(directory) / phase, policy, 'low', cap, command, native)
            result = await Coordinator(.3, 'cursor/grok-4.7-low', worker, decision, advisor, lambda *args: None).run('synthetic task')

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(expert_inputs), 1)
        actual = json.loads(gates[0]['worker_evidence']['text'])['actual_command_results']
        self.assertEqual(actual[-1]['exit_code'], 7)
        self.assertEqual(actual[-1]['stdout'], 'observed failed invariant')
        self.assertEqual(expert_inputs[0]['worker_evidence'], gates[0]['worker_evidence'])
        self.assertEqual(result['terminal_reason'], 'deliver')
        self.assertEqual(result['integrations'], 0)
        self.assertAlmostEqual(result['cost_usd'], .092)


if __name__ == '__main__':
    unittest.main()
