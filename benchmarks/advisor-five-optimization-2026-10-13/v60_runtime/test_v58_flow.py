import json
import unittest
from pathlib import Path
from .coordinator import Coordinator
from .policy import INITIAL_WORK, preparation_images, ADVISOR, DECISION

ROOT = Path(__file__).resolve().parent


class V58FlowTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, baseline=.4645816, worker_cost=.004, worker_status='needs_advisor', first_action='advisor',
                       advisor_cost=.2, second_action='deliver'):
        work = []; calls = []; emitted = {}; gates = []; planned = []
        async def planner(*args):
            planned.append(args); raise AssertionError('V58 must not call the planner')
        async def worker(phase, payload, policy, cap):
            work.append((phase, payload, policy, cap))
            return {'status': worker_status, 'usage': {'cost_usd': worker_cost if len(work) == 1 else .002},
                    'evidence': {'text': json.dumps({'status': worker_status, 'summary': 'actual attempt'})}}
        base = {'issue': 'Specialist part unresolved', 'evidence': 'Worker reported doubt', 'paths': ['/app/work.py'],
                'intervention': 'Review and complete', 'acceptance': 'Original deliverable checked'}
        async def decision(phase, payload):
            gates.append(payload)
            action = first_action if len(gates) == 1 else second_action
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'decision': dict(base, action=action)}
        async def advisor(phase, payload):
            calls.append(payload)
            return {'status': 'completed', 'usage': {'cost_usd': advisor_cost},
                    'evidence': {'text': '{"status":"completed","summary":"done"}'}}
        async def finalizer(*args): return None
        c = Coordinator(baseline, 'gpt-6-luna', worker, decision, advisor, lambda k, v: emitted.update({k: v}), planner, finalizer)
        result = await c.run('Complete original assignment')
        return result, work, calls, emitted, planned, c

    async def test_ceiling_is_73_percent_of_solo(self):
        r, *_ = await self.scenario()
        self.assertAlmostEqual(r['target_ceiling_usd'], .4645816 * .73)

    async def test_worker_gets_full_task_first_without_planner(self):
        r, w, c, e, planned, _ = await self.scenario()
        self.assertFalse(planned)
        phase, payload, policy, cap = w[0]
        self.assertEqual(phase, 'worker-discovery')
        self.assertEqual(payload['original_task'], 'Complete original assignment')
        self.assertNotIn('work_order', payload)
        self.assertIs(policy, INITIAL_WORK)
        self.assertAlmostEqual(cap, .4645816 * .06)
        self.assertEqual(len(c), 1)
        self.assertEqual(r['terminal_reason'], 'deliver')

    async def test_worker_claimed_completion_still_gets_one_expert_review(self):
        r, w, c, e, *_ = await self.scenario(worker_status='completed', first_action='deliver')
        self.assertEqual(len(c), 1)
        self.assertEqual(e['advisor-scope-promotion-0']['reason'], 'component_is_not_original_deliverable')
        self.assertIn('not the deliverable', c[0]['contract']['acceptance'])

    async def test_routine_repair_uses_original_task_and_bounded_share(self):
        r, w, c, *_ = await self.scenario(first_action='prepare')
        self.assertEqual(w[1][0], 'worker-preparation-0')
        self.assertEqual(w[1][1]['original_task'], 'Complete original assignment')
        self.assertNotIn('work_order', w[1][1])
        self.assertAlmostEqual(w[1][3], .4645816 * .10 - .004)

    async def test_all_spend_counts_and_overshoot_kept(self):
        r, *_ = await self.scenario(advisor_cost=.5)
        self.assertGreater(r['cost_usd'], r['target_ceiling_usd'])
        self.assertTrue(r['usage_complete'])

    def test_worker_sees_original_images(self):
        images = [{'data': 'x', 'mimeType': 'image/png'}]
        for phase in ['worker-discovery', 'worker-preparation-0', 'worker-implementation-2']:
            self.assertEqual(preparation_images(phase, images), images)

    def test_policies_are_task_general(self):
        for text in [INITIAL_WORK, ADVISOR, DECISION]:
            for word in ['satb', 'wal', 'cad', 'hof', 'atrx', 'musicxml', 'step file']:
                self.assertNotIn(word, text.lower().split())

    def test_assignment_uses_low_cost_luna_worker_everywhere(self):
        a = json.loads((ROOT / 'assignment.json').read_text())
        original = json.loads((ROOT.parent / 'manifest.json').read_text())['assignment']
        self.assertEqual(set(a), set(original))
        for task, row in a.items():
            self.assertEqual(row['worker'], 'gpt-6-luna')
            for k in ['advisor', 'advisor_effort', 'decision', 'decision_effort']:
                self.assertEqual(row[k], original[task][k])


if __name__ == '__main__':
    unittest.main()


class V58EvidenceTests(unittest.TestCase):
    def test_bounded_copy_keeps_exit_codes_and_marks_omissions(self):
        from .evidence import compact_payload, MAX_OUTPUT_CHARS
        cmds = [{'command': 'x' * 5000, 'exit_code': i % 2, 'output': 'y' * 9000} for i in range(9)]
        payload = {'worker_evidence': {'text': 'not json', 'commands': cmds}}
        out = compact_payload(payload)['worker_evidence']
        self.assertEqual(len(out['commands']), 6)
        self.assertEqual(out['earlier_commands_omitted'], 3)
        self.assertEqual([c['exit_code'] for c in out['commands']], [c['exit_code'] for c in cmds[-6:]])
        self.assertLess(len(out['commands'][0]['output']), MAX_OUTPUT_CHARS + 120)
        self.assertEqual(len(payload['worker_evidence']['commands'][0]['output']), 9000)

    def test_small_evidence_unchanged_except_marker(self):
        from .evidence import compact_payload
        ev = {'text': 'ok', 'commands': [{'command': 'ls', 'exit_code': 0, 'output': 'a'}]}
        out = compact_payload({'worker_evidence': ev})['worker_evidence']
        self.assertEqual(out['commands'], ev['commands'])


class V59UnsplitBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_expert_gets_whole_available_allowance(self):
        calls = []
        async def planner(*a): raise AssertionError
        async def worker(*a): return {'status': 'needs_advisor', 'usage': {'cost_usd': .02}, 'evidence': {'text': 'x'}}
        async def decision(phase, ctx):
            return {'status': 'completed', 'usage': {'cost_usd': .001}, 'decision': {'action': 'advisor' if not calls else 'deliver',
                    'issue': 'i', 'evidence': 'e', 'paths': [], 'intervention': 'do', 'acceptance': 'a'}}
        async def advisor(phase, payload):
            calls.append(payload); return {'status': 'completed', 'usage': {'cost_usd': .1}, 'evidence': {'text': '{"status":"completed"}'}}
        async def fin(*a): return None
        c = Coordinator(.5, 'gpt-6-luna', worker, decision, advisor, lambda k, v: None, planner, fin)
        await c.run('task')
        self.assertAlmostEqual(calls[0]['cost_target_usd'], .5*.73 - .021 - .005)
        self.assertAlmostEqual(calls[0]['review_checkpoint']['correction_reserve_usd'], 0.)


class V60IsolationTests(unittest.TestCase):
    def test_controller_wrappers_use_isolated_interpreter(self):
        for name in ['environment.py', 'remote.py']:
            text = (ROOT / name).read_text()
            self.assertIn("'python3 -I -c '", text)
            self.assertNotIn("'python3 -c '", text)

    def test_isolated_interpreter_ignores_shadowing_workspace_files(self):
        import subprocess, sys, tempfile
        with tempfile.TemporaryDirectory() as d:
            for mod in ['inspect', 'json', 'subprocess']:
                Path(d, mod + '.py').write_text("raise SystemExit('shadowed')\n")
            code = 'import importlib.metadata,json,subprocess;list(importlib.metadata.distributions());print("ok")'
            plain = subprocess.run([sys.executable, '-c', code], cwd=d, capture_output=True, text=True)
            isolated = subprocess.run([sys.executable, '-I', '-c', code], cwd=d, capture_output=True, text=True)
            self.assertNotEqual(plain.stdout.strip(), 'ok')
            self.assertEqual(isolated.stdout.strip(), 'ok')

    def test_worker_confined_to_worker_directory(self):
        self.assertIn('/app/worker/', INITIAL_WORK)
        self.assertIn('Do not create or overwrite the task', INITIAL_WORK)
        self.assertIn('/app/worker/', ADVISOR)
