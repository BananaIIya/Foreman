import asyncio
import copy
import io
import json
import unittest

from .client_tools import ClientToolsMixin
from .delegation import InTurnWorker, TOOL


WORK = {'work_kind':'implementation', 'procedure': 'Read the supplied CSV and implement the specified column mapping.',
        'input_paths': ['/app/input.csv'], 'output_paths': ['/app/mapped.csv'],
        'given_facts':'The existing header establishes the supplied mapping.',
        'hypothesis':'','decision_rule':'Retain the original values under the specified mapped columns.',
        'acceptance_checks': ['Verify row count and mapped field values against the input.']}


class DelegationTests(unittest.IsolatedAsyncioTestCase):
    def make(self, worker, remaining=lambda: .1, limit=.04, reserve=.01):
        self.events = []
        return InTurnWorker(worker, remaining, limit, reserve,
                            lambda name, row: self.events.append((name, row)))

    async def test_duplicate_pending_executes_once_and_retains_failure(self):
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def worker(work, cap):
            calls.append(cap); started.set(); await finish.wait()
            return {'status': 'needs_advisor', 'usage': {'cost_usd': .05},
                    'evidence': {'commands': [{'exit_code': 1, 'output': 'actual failure'}]}}
        bridge = self.make(worker)
        a = asyncio.create_task(bridge.invoke('one', WORK)); await started.wait()
        b = asyncio.create_task(bridge.invoke('one', copy.deepcopy(WORK)))
        conflict = await bridge.invoke('one', dict(WORK, procedure='Different operation'))
        second = await bridge.invoke('two', WORK)
        finish.set(); first, replay = await asyncio.gather(a, b)
        self.assertEqual(first, replay); self.assertEqual(calls, [.04])
        self.assertEqual(conflict['status'], 'call_id_conflict')
        self.assertEqual(second['model_calls'], 0)
        self.assertEqual(first['evidence']['commands'][0]['exit_code'], 1)
        self.assertEqual(bridge.spent, .05)  # Native boundary overshoot is never clipped.
        self.assertEqual(len(bridge.records), 1)

    async def test_cancelled_waiter_does_not_cancel_paid_work(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def worker(work, cap):
            started.set(); await finish.wait()
            return {'status': 'completed', 'usage': {'cost_usd': .02}, 'evidence': {}}
        bridge = self.make(worker)
        waiter = asyncio.create_task(bridge.invoke('one', WORK)); await started.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiter
        settling = asyncio.create_task(bridge.settle()); await asyncio.sleep(0)
        self.assertFalse(settling.done()); finish.set(); await settling
        self.assertEqual(bridge.spent, .02)
        self.assertEqual(len(bridge.records), 1)

    async def test_invalid_unknown_and_exhausted_budget_never_call_worker(self):
        calls = []
        async def worker(work, cap): calls.append(work); raise AssertionError()
        for remaining in [lambda: None, lambda: float('nan'), lambda: .005]:
            bridge = self.make(worker, remaining)
            result = await bridge.invoke('one', WORK)
            self.assertEqual(result['model_calls'], 0)
        bridge = self.make(worker)
        for path in ['/tests/answers.json', '/app/../tests/answers.json', '/app/.codex/auth.json']:
            result = await bridge.invoke('one', dict(WORK, input_paths=[path]))
            self.assertEqual(result['status'], 'invalid_work_order')
        self.assertEqual(calls, [])

    async def test_unknown_usage_and_exception_are_not_zero(self):
        for value in [None, -1, float('nan'), True]:
            async def worker(work, cap):
                return {'status': 'completed', 'usage': {'cost_usd': value}}
            bridge = self.make(worker); result = await bridge.invoke('one', WORK)
            self.assertFalse(result['usage_complete'])
            self.assertIsNone(result['actual_cost_usd'])
            self.assertEqual((await bridge.invoke('two', WORK))['model_calls'], 0)
        async def broken(work, cap): raise RuntimeError('Transport ended')
        bridge = self.make(broken); result = await bridge.invoke('one', WORK)
        self.assertIsNone(result['actual_cost_usd']); self.assertEqual(len(bridge.records), 1)

    async def test_reserve_is_subtracted_and_closed_bridge_cannot_launch(self):
        async def worker(work, cap):
            self.assertAlmostEqual(cap, .02)
            return {'status': 'completed', 'usage': {'cost_usd': .01}}
        bridge = self.make(worker, lambda: .03)
        await bridge.invoke('one', WORK); await bridge.settle()
        self.assertEqual((await bridge.invoke('two', WORK))['model_calls'], 0)


class FakeBase:
    async def rpc(self, method, params): return params
    async def close(self): self.base_closed = True


class FakeClient(ClientToolsMixin, FakeBase):
    def __init__(self):
        self.proc = type('Proc', (), {'stdout': asyncio.StreamReader()})()
        self.events = io.StringIO(); self.pending = {}; self.sent = []
        self.notifications = asyncio.Queue()
    async def send(self, message): self.sent.append(message)
    def feed(self, message):
        self.proc.stdout.feed_data((json.dumps(message)+'\n').encode())


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_worker_does_not_block_usage_or_rpc(self):
        client = FakeClient(); started, finish = asyncio.Event(), asyncio.Event()
        async def handler(params):
            started.set(); await finish.wait(); return {'status': 'completed'}
        client.configure_client_tools([TOOL], handler)
        reader = asyncio.create_task(client.read())
        client.feed({'id': 'tool1', 'method': 'item/tool/call', 'params': {}})
        await started.wait()
        response = asyncio.get_running_loop().create_future(); client.pending[7] = response
        client.feed({'method': 'thread/tokenUsage/updated', 'params': {'test': True}})
        client.feed({'id': 7, 'result': {'turnId': 'still-same-turn'}})
        notification = await asyncio.wait_for(client.notifications.get(), 1)
        self.assertEqual(notification['method'], 'thread/tokenUsage/updated')
        self.assertEqual(await asyncio.wait_for(response, 1), {'turnId': 'still-same-turn'})
        self.assertEqual(client.sent, [])
        finish.set(); await client.settle_client_tools()
        self.assertEqual(client.sent[0]['id'], 'tool1')
        self.assertTrue(client.sent[0]['result']['success'])
        client.proc.stdout.feed_eof(); await reader

    async def test_approval_request_is_never_implicitly_approved(self):
        client = FakeClient(); calls = []
        async def handler(params): calls.append(params)
        client.configure_client_tools([TOOL], handler)
        client.feed({'id': 1, 'method': 'item/commandExecution/requestApproval', 'params': {}})
        client.proc.stdout.feed_eof(); await client.read()
        self.assertEqual(calls, []); self.assertIn('error', client.sent[0])

    async def test_registration_preserves_existing_native_configuration(self):
        client = FakeClient(); client.configure_client_tools([TOOL], None)
        original = {'model': 'gpt-6.1-sol', 'config': {'model_reasoning_effort': 'high'}}
        result = await client.rpc('thread/start', original)
        self.assertEqual(result['dynamicTools'], [TOOL]); self.assertNotIn('dynamicTools', original)
        self.assertEqual(result['config'], original['config'])
        self.assertEqual(await client.rpc('thread/resume', original), original)


if __name__ == '__main__': unittest.main()
