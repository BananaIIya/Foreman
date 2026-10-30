import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hybrid_native import Session
from .native import AdvisorSession
from .representation import artifact_excerpt


class ObservationTests(unittest.IsolatedAsyncioTestCase):
    def test_large_geometry_is_explicitly_metadata_not_a_correctness_claim(self):
        data = b'ISO-10303-21;\n' + b'point data\n' * 10000
        row = artifact_excerpt('/app/result.step', data)
        self.assertNotIn('text', row)
        self.assertEqual(row['bytes'], len(data))
        self.assertTrue(row['content_omitted'])
        self.assertIn('does not establish task correctness', row['note'])
        self.assertLess(len(json.dumps(row)), 650)

    def test_small_json_is_complete_and_large_text_is_marked_truncated(self):
        row = artifact_excerpt('/app/report.json', b'{ "error": "failed", "count": 0 }')
        self.assertEqual(json.loads(row['text']), {'error': 'failed', 'count': 0})
        self.assertFalse(row['truncated'])
        large = artifact_excerpt('/app/code.py', b'a' * 9000, 300)
        self.assertEqual(len(large['text']), 300)
        self.assertTrue(large['truncated'])
        self.assertEqual(large['original_characters'], 9000)

    def test_binary_and_exhausted_excerpt_budget_are_not_missing_files(self):
        binary = artifact_excerpt('/app/data.bin', b'\xff\xfe')
        self.assertEqual(binary['kind'], 'binary_metadata')
        empty = artifact_excerpt('/app/code.py', b'print(1)', 0)
        self.assertTrue(empty['truncated'])
        self.assertEqual(empty['text'], '')
        self.assertNotIn('unavailable', empty)

    async def test_start_and_resume_preserve_model_tools_and_default_web(self):
        captured = []
        async def rpc(instance, method, params):
            captured.append((method, copy.deepcopy(params)))
            return {'ok': True}
        with tempfile.TemporaryDirectory() as folder:
            client = object.__new__(AdvisorSession)
            client.log_dir = Path(folder)
            params = {'model': 'gpt-6.1-sol', 'environments': [{'environmentId': 'daytona'}],
                      'config': {'model_reasoning_effort': 'high', 'web_search': 'disabled',
                                 'features': {'multi_agent': False, 'unified_exec': True}}}
            original = copy.deepcopy(params)
            with patch.object(Session, 'rpc', rpc):
                for method in ('thread/start', 'thread/resume'):
                    await client.rpc(method, params)
            self.assertEqual(params, original)
            for _, received in captured:
                self.assertEqual(received['config']['tool_output_token_limit'], 2048)
                self.assertNotIn('web_search', received['config'])
                self.assertEqual(received['model'], 'gpt-6.1-sol')
                self.assertEqual(received['config']['model_reasoning_effort'], 'high')
                self.assertFalse(received['config']['features']['multi_agent'])
                self.assertEqual(received['environments'], original['environments'])


if __name__ == '__main__':
    unittest.main()
