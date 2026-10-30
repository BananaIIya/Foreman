import copy
import json
import unittest
from .evidence import compact_evidence


class EvidenceTests(unittest.TestCase):
    def test_failed_and_successful_records_round_trip_without_mutating_raw_evidence(self):
        commands = [{'command': 'first', 'exit_code': 9, 'stderr': 'important error', 'stdout': 'a'*500},
                    {'command': 'repair', 'exit_code': 0, 'stderr': '', 'stdout': 'actual observation'}]
        report = {'status': 'preparation_observed', 'actual_command_results': commands,
                  'critical_question': 'Unresolved specialist question', 'relevant_paths': ['/app/input'],
                  'protocol_history': [{'actual_command_results': commands[:1], 'unexecuted_commands': ['skipped']},
                                       {'actual_command_results': commands[1:]}]}
        original = {'text': json.dumps(report), 'commands': commands, 'command_count': 2}
        snapshot = copy.deepcopy(original)
        compact = compact_evidence(original)
        self.assertEqual(original, snapshot)
        self.assertEqual(compact['commands'], commands)
        decoded = json.loads(compact['text'])
        decoded.pop('actual_command_results_reference')
        decoded['actual_command_results'] = compact['commands']
        for entry in decoded['protocol_history']:
            entry['actual_command_results'] = [compact['commands'][i] for i in entry.pop('actual_command_result_indexes')]
        self.assertEqual(decoded, report)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(original)))

    def test_partial_or_mismatched_records_are_not_removed(self):
        original = {'text': json.dumps({'actual_command_results': [{'exit_code': 0}]}),
                    'commands': [{'exit_code': 9}], 'command_count': 1}
        self.assertEqual(compact_evidence(original), original)

    def test_native_prose_and_invalid_json_are_preserved(self):
        for text in ('Actual native report', '{"unfinished":'):
            original = {'text': text, 'commands': [{'exit_code': 9}]}
            self.assertEqual(compact_evidence(original), original)


if __name__ == '__main__':
    unittest.main()
