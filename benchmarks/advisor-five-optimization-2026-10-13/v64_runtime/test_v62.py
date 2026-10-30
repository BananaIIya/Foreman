import json
import tempfile
import unittest
from pathlib import Path
from .transcript import render, completed_items
from .advisor import Advisor, ADVISOR_TOOL, EXECUTOR_POLICY, ADVISOR_POLICY, MAX_CALLS


def write_events(path, items):
    path.write_text(''.join(json.dumps({'method': 'item/completed', 'params': {'item': it}}) + '\n' for it in items))


class TranscriptTests(unittest.TestCase):
    def test_render_bounds_and_keeps_exit_codes(self):
        text = render([{'type': 'commandExecution', 'command': 'x' * 5000, 'exitCode': 3, 'aggregatedOutput': 'y' * 9000},
                       {'type': 'agentMessage', 'text': 'hello'},
                       {'type': 'fileChange', 'changes': [{'path': '/app/a.py', 'diff': '+1'}]}])
        self.assertIn('exit 3', text)
        self.assertIn('EXECUTOR: hello', text)
        self.assertIn('EDIT /app/a.py', text)
        self.assertLess(len(text), 5000)

    def test_reasoning_and_hidden_items_not_rendered(self):
        self.assertEqual(render([{'type': 'reasoning', 'text': 'secret'}]), '')


class FinalReviewTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.events = Path(self.dir.name) / 'events.jsonl'
        self.adv = Advisor(self.dir.name, self.dir.name, self.events, 'task', 'gpt-6.1-sol', 'high', [])

    def tearDown(self):
        self.dir.cleanup()

    def test_nudge_when_no_call_at_all(self):
        write_events(self.events, [{'type': 'commandExecution', 'command': 'ls', 'exitCode': 0}])
        self.assertTrue(self.adv.needs_final_review())

    def test_nudge_when_work_happened_after_last_advice(self):
        write_events(self.events, [{'type': 'commandExecution'}, {'type': 'dynamicToolCall'}, {'type': 'fileChange', 'changes': []}])
        self.adv.calls = [{'call': 1, 'ok': True, 'items_seen': 1}]
        self.assertTrue(self.adv.needs_final_review())

    def test_no_nudge_after_final_review(self):
        write_events(self.events, [{'type': 'commandExecution'}, {'type': 'fileChange', 'changes': []}, {'type': 'dynamicToolCall'}, {'type': 'agentMessage', 'text': 'done'}])
        self.adv.calls = [{'call': 1, 'ok': True, 'items_seen': 2}]
        self.assertFalse(self.adv.needs_final_review())

    def test_nudge_when_executor_gave_up_without_any_command(self):
        write_events(self.events, [{'type': 'agentMessage', 'text': 'cannot do this'}])
        self.assertTrue(self.adv.needs_final_review())

    def test_no_nudge_when_limit_used(self):
        write_events(self.events, [{'type': 'commandExecution'}])
        self.adv.calls = [{'call': i, 'ok': True, 'items_seen': 0} for i in range(1, MAX_CALLS + 1)]
        self.assertFalse(self.adv.needs_final_review())

    def test_limit_enforced_without_model_call(self):
        import asyncio
        write_events(self.events, [])
        self.adv.calls = [{'call': i, 'ok': True, 'items_seen': 0} for i in range(1, MAX_CALLS + 1)]
        r = asyncio.run(self.adv.handle({'tool': 'advisor', 'arguments': {'question': 'x'}}))
        self.assertFalse(r['success'])


class PolicyTests(unittest.TestCase):
    def test_policies_are_task_general_and_bounded(self):
        for text in (EXECUTOR_POLICY, ADVISOR_POLICY):
            for word in ('satb', 'wal', 'cad', 'hof', 'atrx', 'musicxml'):
                self.assertNotIn(word, text.lower().split())
        self.assertIn('250 words', ADVISOR_POLICY)
        self.assertEqual(ADVISOR_TOOL['inputSchema']['required'], ['question'])


class V63ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_calls_are_serialized_with_distinct_numbers(self):
        import asyncio
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / 'events.jsonl'
            write_events(events, [{'type': 'commandExecution'}])
            adv = Advisor(d, d, events, 'task', 'gpt-6.1-sol', 'high', [])
            async def fake_sol(folder, items, question, call):
                await asyncio.sleep(0.05)
                return f'advice {call}', {'cost_usd': 0.01}
            adv._sol = fake_sol
            r1, r2 = await asyncio.gather(adv.handle({'tool': 'advisor', 'arguments': {'question': 'a'}}),
                                          adv.handle({'tool': 'advisor', 'arguments': {'question': 'b'}}))
            self.assertTrue(r1['success'] and r2['success'])
            self.assertEqual([c['call'] for c in adv.calls], [1, 2])

    def test_abandoned_call_cost_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            adv = Advisor(d, d, Path(d) / 'e.jsonl', 'task', 'gpt-6.1-sol', 'high', [])
            adv.in_flight = 2
            adv.abandon_in_flight()
            self.assertIsNone(adv.calls[0]['cost_usd'])
            self.assertFalse(adv.calls[0]['ok'])
