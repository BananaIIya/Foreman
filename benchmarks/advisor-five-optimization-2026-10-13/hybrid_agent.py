"""Checkpointed worker, independent decision model, shared-workspace specialist.

The coordinator never solves a task or reads hidden test/reference contents.
All candidate outputs come from the selected native worker/advisor models.
"""
import base64
import contextlib
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from remote_native_codex import RemoteNativeCodex
from hybrid_native import Session, account_codex, read_events
from hybrid_cursor import cursor_turn, install_cursor
from routing import (ADVISOR, DECISION, DECISION_SCHEMA, DISCOVER, WORKER, WORKER_SYSTEM,
                     Budget, choose_action, evidence_fingerprint, usage_cost)
from shared_workspace import executor, make_home

ROOT = Path(__file__).resolve().parent


def parse_json(text):
    decoder = json.JSONDecoder()
    for pos, ch in enumerate(text):
        if ch == '{':
            try:
                obj, _ = decoder.raw_decode(text[pos:])
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                pass
    return {}


def baseline_cost(task):
    paths = list((ROOT / 'raw/jobs' / ('solo-' + task)).glob('*/result.json'))
    assert len(paths) == 1
    result = json.loads(paths[0].read_text())
    assert result['exception_info'] is None
    # A valid wrong solo is retained as an attempt-cost baseline; it must not
    # silently remove this task from the five-task cohort. Reports separately
    # require a correct solo for the stricter solved-task savings comparison.
    assert result['verifier_result']['rewards']['reward'] in (0, 1), 'Need a real scored advisor attempt'
    logs = paths[0].parent / 'agent'
    if (logs / 'app-server.events.jsonl').exists():
        cost = account_codex(read_events(logs / 'app-server.events.jsonl'), 'gpt-6.1-sol')['cost_usd']
    else:
        events = read_events(logs / 'cursor-cli.txt')
        (done,) = [e for e in events if e.get('type') == 'result']
        cost = usage_cost('grok-4.7', done['usage'], 'cursor')
    assert cost is not None and cost > 0
    return cost


class HybridAgent(RemoteNativeCodex):
    def populate_context_post_run(self, context):
        from metrics import aggregate_phases
        from harbor.models.agent.context import ModelUsage
        totals = aggregate_phases(self.logs_dir / 'phases')
        context.cost_usd = totals['cost_usd']
        context.metadata = {'mixed_native_usage_complete': totals['complete'],
                            'role_cost_usd': totals['role_cost_usd'],
                            'known_cost_lower_bound_usd': totals['known_cost_lower_bound_usd'],
                            'cost_basis': 'standard token list-price equivalent, not a subscription invoice'}
        if totals['complete']:
            context.model_usage = {m: ModelUsage(**row) for m, row in totals['models'].items()}
            for key in ['n_input_tokens', 'n_cache_tokens', 'n_output_tokens']:
                setattr(context, key, sum(row[key] for row in totals['models'].values()))

    async def install(self, environment):
        await super().install(environment)
        await install_cursor(self, environment)

    async def run(self, instruction, environment, context):
        task = environment.environment_dir.parent.name
        manifest = json.loads((ROOT / 'manifest.json').read_text())
        assignment = manifest['assignment'][task]
        self.logs_dir.mkdir(exist_ok=True, parents=True)
        logs = self.logs_dir / 'phases'; logs.mkdir(exist_ok=True)
        private = Path(tempfile.mkdtemp(prefix='advisor-five-', dir='/private/tmp'))
        budget = Budget(baseline_cost(task))
        records = []
        worker_session = None
        issue_history = []
        stage = 'discovery'
        terminal_reason = None

        def record(role, phase, result):
            budget.record(result['usage']['cost_usd'])
            entry = {'role': role, 'phase': phase, 'cost_usd': result['usage']['cost_usd'],
                     'status': result['status'], 'spent_usd': budget.spent}
            records.append(entry)
            (self.logs_dir / 'ledger.json').write_text(json.dumps(records, indent=2) + '\n')

        async def codex(role, phase, model, effort, prompt, policy, url,
                        session_id=None, cap=None, schema=None, image_inputs=None):
            home = make_home(private, role, model, effort)
            work = private / 'empty'; work.mkdir(exist_ok=True)
            client = Session(home, logs / phase, model, effort)
            try:
                await client.start()
                return await client.turn(url, prompt, policy, '/app' if url else str(work),
                    thread_id=session_id, schema=schema, extra_input=image_inputs,
                    budget_usd=cap, no_tools=url is None)
            finally:
                await client.close()

        async def worker(phase, prompt, policy, url):
            nonlocal worker_session
            cap = max(0, budget.ceiling - budget.spent)
            if assignment['worker'].startswith('cursor/'):
                result = await cursor_turn(self, environment, logs / phase,
                    WORKER_SYSTEM + '\n\n' + policy + '\n\n' + prompt + f'\n\nRemaining combined cost target: ${cap:.5f}. Keep this phase focused.',
                    assignment['worker_effort'])
                worker_session = result['session_id']
            else:
                result = await codex('worker', phase, assignment['worker'], assignment['worker_effort'],
                    policy + '\n\n' + prompt, WORKER_SYSTEM, url, session_id=worker_session, cap=cap)
                worker_session = result['thread_id']
            record('worker', phase, result)
            return result

        async def input_images():
            # Original task inputs only; never tests, solutions, prior artifacts.
            result = []
            for path in sorted(environment.environment_dir.rglob('*')):
                if path.suffix.lower() not in ('.png', '.jpg', '.jpeg') or path.stat().st_size > 4_000_000:
                    continue
                data = path.read_bytes()
                mime = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
                result.append({'type': 'image', 'url': f'data:{mime};base64,' + base64.b64encode(data).decode(), 'detail': 'original'})
                if len(result) == 2:
                    break
            return result

        async def workspace_excerpts(evidence):
            reported = parse_json(evidence.get('text', ''))
            excerpts = []
            for candidate in reported.get('relevant_paths', [])[:4]:
                p = Path(candidate)
                if not p.is_absolute():
                    p = Path('/app') / p
                if not str(p).startswith('/app/') or '..' in p.parts or p.suffix not in ('.py', '.json', '.csv', '.md', '.txt', '.js', '.mjs', '.ts', '.c', '.cpp', '.rs'):
                    continue
                dest = private / ('excerpt-' + hashlib.sha256(str(p).encode()).hexdigest()[:16])
                try:
                    await environment.download_file(str(p), dest)
                    if dest.stat().st_size <= 30000:
                        excerpts.append({'path': str(p), 'text': dest.read_text()})
                except Exception:
                    # Missing files are evidence gaps, not fabricated content.
                    excerpts.append({'path': str(p), 'unavailable': True})
            return excerpts

        try:
            async with executor(self, environment, private) as url:
                result = await worker('worker-discovery', 'Original task:\n' + instruction, DISCOVER, url)
                for cycle in range(4):
                    if not budget.can_continue():
                        terminal_reason = 'budget_exhausted'; break
                    evidence = result['evidence']
                    gate_prompt = json.dumps({'stage': stage, 'original_task': instruction,
                        'worker_evidence': evidence, 'workspace_excerpts': await workspace_excerpts(evidence),
                        'previous_interventions': [r for r in records if r['role'] == 'advisor'],
                        'self_checks_used': budget.self_checks, 'advisor_calls_used': budget.advisor_calls}, ensure_ascii=False)
                    gate = await codex('decision-' + str(cycle), 'decision-' + str(cycle),
                        'gpt-6-luna', 'medium', gate_prompt, DECISION, None,
                        schema=DECISION_SCHEMA, image_inputs=await input_images())
                    record('decision', 'decision-' + str(cycle), gate)
                    decision = parse_json(gate['evidence']['text'])
                    import jsonschema
                    jsonschema.validate(decision, DECISION_SCHEMA)
                    action = choose_action(decision, stage, budget, issue_history)
                    (logs / ('decision-' + str(cycle)) / 'routing.json').write_text(json.dumps(
                        {'decision': decision, 'action': action, 'advisor_allowance_usd': budget.advisor_allowance()}, indent=2) + '\n')
                    if action in ('deliver', 'budget_exhausted', 'repeated_issue'):
                        terminal_reason = action; break
                    if action == 'advisor':
                        allowance = budget.advisor_allowance()
                        prompt = ('Original task:\n' + instruction + '\n\nIntervention contract:\n' +
                                  json.dumps(decision) + '\n\nCurrent worker evidence:\n' +
                                  json.dumps(evidence) + f'\n\nThis intervention has a ${allowance:.5f} target; leave integration and final verification to the worker.')
                        phase = 'advisor-' + str(budget.advisor_calls)
                        if assignment['advisor'].startswith('cursor/'):
                            advice = await cursor_turn(self, environment, logs / phase,
                                ADVISOR + '\n\n' + prompt, assignment['advisor_effort'])
                        else:
                            advice = await codex('advisor-' + str(budget.advisor_calls), phase,
                                assignment['advisor'], assignment['advisor_effort'], prompt, ADVISOR, url, cap=allowance)
                        record('advisor', phase, advice)
                        budget.advisor_calls += 1
                        issue_history.append(evidence_fingerprint(decision))
                        outcome = ('completed' if advice['status'] == 'completed' else
                                   'stopped before completion; inspect any partial edits and do not assume they are validated')
                        handoff = 'Specialist intervention status: ' + outcome + '. Current evidence:\n' + json.dumps(advice['evidence'])
                    else:
                        if action == 'self_check':
                            budget.self_checks += 1
                        handoff = 'Coordinator checkpoint:\n' + json.dumps(decision)
                    if not budget.can_continue():
                        terminal_reason = 'budget_exhausted'; break
                    result = await worker('worker-implementation-' + str(cycle),
                        WORKER + '\n\nOriginal task:\n' + instruction + '\n\n' + handoff, WORKER, url)
                    stage = 'delivery'
                if terminal_reason is None:
                    terminal_reason = 'maximum_checkpoints_reached'
                final = {'task': task, 'assignment': assignment, 'terminal_reason': terminal_reason,
                         'baseline_cost_usd': budget.baseline, 'cost_usd': budget.spent,
                         'usage_complete': budget.usage_complete, 'target_ceiling_usd': budget.ceiling,
                         'advisor_calls': budget.advisor_calls, 'self_checks': budget.self_checks,
                         'phases': records, 'instruction_sha256': hashlib.sha256(instruction.encode()).hexdigest(),
                         'verifier_feedback': False, 'advisor_workspace_access': True,
                         'cursor_history_strategy': 'fresh phase with current workspace and compact handoff; each native usage result counted once',
                         'budget_note': 'Checked at response/phase boundaries; a single native response can overshoot. Overshoot is fully charged, never omitted.'}
                (self.logs_dir / 'hybrid-result.json').write_text(json.dumps(final, indent=2) + '\n')
        finally:
            with contextlib.suppress(Exception):
                await environment.upload_dir(self.logs_dir, str(self.environment_logs_dir))
            shutil.rmtree(private)
