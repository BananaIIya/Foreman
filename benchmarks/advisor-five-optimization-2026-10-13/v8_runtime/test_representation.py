import json
import unittest
from types import SimpleNamespace
from v8_runtime.representation import compact_json, bounded_text
from v8_runtime.execution import execute_commands
from v8_runtime.policy import validate_patch
from v8_runtime.test_flow import patch


class RepresentationTests(unittest.TestCase):
    def test_large_pretty_diagnostic_keeps_final_record(self):
        records = [{'sample': i, 'measurements': list(range(200))} for i in range(7)]
        raw = json.dumps(records, indent=4)
        self.assertGreater(len(raw), 18000)
        result = bounded_text(raw, 18000)
        self.assertFalse(result['truncated'])
        self.assertEqual(json.loads(result['text']), records)

    def test_numeric_lexemes_duplicate_keys_and_string_spaces_preserved(self):
        raw = ' { "x": 123456789.123456789, "x": 1e-099, "s": " a \\" b " } '
        self.assertEqual(compact_json(raw), '{"x":123456789.123456789,"x":1e-099,"s":" a \\" b "}')

    def test_non_json_is_not_reformatted_or_labeled_complete_when_truncated(self):
        raw = 'log  with  whitespace\n' * 100
        result = bounded_text(raw, 500)
        self.assertEqual(result['text'], raw[:500])
        self.assertTrue(result['truncated'])
        self.assertFalse(result['json_whitespace_compacted'])

    def test_evidence_paths_reject_escape(self):
        for path in ['/tests/answer', '/app/../tests/answer', '/app/.agents/config']:
            candidate = dict(patch('needs_context'), evidence_paths=[path])
            with self.assertRaises(ValueError):
                validate_patch(candidate)


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_preserves_large_output_flags_and_artifact_paths(self):
        candidate = dict(patch('needs_context'), evidence_paths=['/app/measurements.json'])
        async def run(command):
            return SimpleNamespace(return_code=0, stdout='x'*25000, stderr='',
                stdout_truncated=True, stderr_truncated=False)
        result = await execute_commands(candidate, run)
        evidence = result['execution']
        self.assertEqual(len(evidence['checks'][0]['stdout']), 25000)
        self.assertTrue(evidence['checks'][0]['stdout_truncated'])
        self.assertEqual(evidence['relevant_paths'][0], '/app/measurements.json')
        self.assertEqual(json.loads(result['evidence']['text']), evidence)


if __name__ == '__main__': unittest.main()
