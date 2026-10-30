import json
import tempfile
import unittest
from pathlib import Path
from .evidence_index import public_commands, project, publish


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        commands = [{'command': 'python implemented-script.py '+str(i), 'exit_code': 0,
                     'output': 'observed data '+('x'*4000), 'output_available': True}
                    for i in range(13)]
        commands[0].update(exit_code=1, output='actual early failure')
        payload = {'original_task': 'Full task', 'cost_target_usd': .1,
                   'worker_evidence': {'text': json.dumps({'checks': [{'exit_code': 0}],
                      'expert_evidence': {'text': 'Still incomplete', 'commands': commands[-10:],
                                          'command_count': 13, 'interrupted': 'budget_boundary_exceeded'}})}}
        return payload, commands

    def test_early_failure_survives_window_and_archive_is_exact(self):
        payload, commands = self.fixture()
        result, raw, receipt = project(payload, commands, '/app/.advisor-evidence-test.json')
        self.assertEqual(json.loads(raw)['original_worker_evidence'], payload['worker_evidence'])
        self.assertEqual(json.loads(raw)['public_commands'], commands)
        expert = json.loads(result['worker_evidence']['text'])['expert_evidence']
        self.assertEqual(expert['command_index'][0]['exit_code'], 1)
        self.assertEqual(expert['command_index'][0]['output'], 'actual early failure')
        self.assertEqual(expert['command_index'][0]['command'], commands[0]['command'])
        self.assertEqual(len(expert['command_index']), 13)
        self.assertFalse(expert['command_index'][1]['excerpt_is_complete'])
        self.assertEqual(result['original_task'], payload['original_task'])
        self.assertEqual(result['cost_target_usd'], payload['cost_target_usd'])
        self.assertLess(receipt['indexed_prompt_characters'], receipt['original_prompt_characters']*.4)
        self.assertFalse(receipt['correctness_proven'])

    def test_only_public_events_and_unconfirmed_output(self):
        events = [{'method': 'item/completed', 'params': {'item': {'id': 'secret', 'type': 'reasoning', 'text': 'private'}}},
                  {'method': 'item/commandExecution/outputDelta', 'params': {'itemId': 'a', 'delta': 'visible'}},
                  {'method': 'item/completed', 'params': {'item': {'id': 'a', 'type': 'commandExecution', 'command': 'check', 'exitCode': 1, 'aggregatedOutput': ''}}},
                  {'method': 'item/completed', 'params': {'item': {'id': 'b', 'type': 'commandExecution', 'command': 'other', 'exitCode': None}}}]
        commands = public_commands(events)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0]['output'], 'visible')
        self.assertFalse(commands[1]['output_available'])
        self.assertNotIn('private', json.dumps(commands))

    def test_count_mismatch_rejected(self):
        payload, commands = self.fixture()
        with self.assertRaises(ValueError):
            project(payload, commands[1:], '/app/.advisor-evidence-test.json')

    async def test_no_reference_until_upload_succeeds(self):
        payload, commands = self.fixture()
        events = [{'method': 'item/completed', 'params': {'item': {'id': str(i),
            'type': 'commandExecution', 'command': c['command'], 'exitCode': c['exit_code'],
            'aggregatedOutput': c['output']}}} for i,c in enumerate(commands)]
        with tempfile.TemporaryDirectory() as d:
            async def fail(*args): raise OSError('upload failed')
            with self.assertRaises(OSError):
                await publish(payload, events, Path(d), 'a'*32, 'advisor-1', fail)
        with tempfile.TemporaryDirectory() as d:
            uploads = []
            async def upload(source, target): uploads.append((source.read_bytes(), target))
            result, receipt = await publish(payload, events, Path(d), 'a'*32, 'advisor-1', upload)
            self.assertEqual(json.loads(uploads[0][0])['public_commands'], commands)
            self.assertTrue(receipt['uploaded'])
            self.assertEqual(receipt['extra_model_calls'], 0)
