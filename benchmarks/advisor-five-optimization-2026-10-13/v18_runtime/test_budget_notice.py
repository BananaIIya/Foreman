import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from .native import AdvisorSession


class NoticeTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, rejected=False):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            client = AdvisorSession(folder, folder, 'gpt-6.1-sol', 'high')
            async def rpc(method, params):
                calls.append((method, params))
                if method == 'thread/start':
                    return {'model': client.model, 'reasoningEffort': client.effort, 'thread': {'id': 'thread'}}
                if method == 'turn/start':
                    return {'turn': {'id': 'turn'}}
                if method == 'turn/steer':
                    if rejected:
                        raise RuntimeError('Synthetic already-finished turn race')
                    return {'turnId': 'turn'}
                return {}
            client.rpc = rpc
            events = []
            total = 0
            for tokens in [8000, 1000, 2000]:
                total += tokens
                usage = {'totalTokens': tokens, 'inputTokens': 0, 'cachedInputTokens': 0,
                         'cacheWriteInputTokens': 0, 'outputTokens': tokens, 'reasoningOutputTokens': 0}
                events.append({'method': 'thread/tokenUsage/updated', 'params': {
                    'threadId': 'thread', 'turnId': 'turn', 'tokenUsage': {
                        'last': usage, 'total': dict(usage, totalTokens=total, outputTokens=total)}}})
            events.append({'method': 'turn/completed', 'params': {'threadId': 'thread',
                           'turn': {'id': 'turn', 'status': 'interrupted'}}})
            (Path(folder)/'app-server.events.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
            for event in events:
                await client.notifications.put(event)
            result = await client.turn('ws://synthetic', 'Synthetic task', 'Synthetic policy', '/app', budget_usd=.1)
            receipt = json.loads((Path(folder)/'budget-notice.json').read_text())
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls), 1)
        self.assertEqual(sum(m=='turn/start' for m,_ in calls), 1)
        self.assertEqual(sum(m=='turn/interrupt' for m,_ in calls), 1)
        self.assertEqual(result['interrupted'], 'budget_boundary_exceeded')
        self.assertAlmostEqual(result['usage']['cost_usd'], .11)
        self.assertEqual(receipt['attempts'], 1)
        self.assertAlmostEqual(receipt['spent_usd'], .08)
        steer = next(p for m,p in calls if m=='turn/steer')
        self.assertEqual(steer['expectedTurnId'], 'turn')
        self.assertEqual(steer['threadId'], 'thread')
        self.assertEqual(receipt['status'], 'not_confirmed' if rejected else 'accepted')

    async def test_single_warning_does_not_change_hard_stop_or_accounting(self):
        await self.exercise()

    async def test_rejected_warning_is_not_replayed_and_does_not_erase_spend(self):
        await self.exercise(rejected=True)


if __name__ == '__main__':
    unittest.main()
