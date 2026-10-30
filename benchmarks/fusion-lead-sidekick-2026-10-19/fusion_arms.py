"""Fusion-style lead + sidekick on Terminal-Bench 4 tasks, Claude models only. Three arms, same tools and base prompt:

OpusSolo    Opus 5.5 does the task alone (control for the lead).
HaikuSolo   Haiku 5.5 does the task alone (control for the sidekick).
HaikuAdvisor  Haiku 5.5 does the task with Claude Code's native `advisor` tool backed by Opus 5.5: the executor is in
            control and asks when it chooses (the Anthropic advisor strategy; the earlier V62-V64 designs in this
            workspace). Same two models as Fusion, opposite direction of control.
Fusion      Opus 5.5 is the LEAD for the whole task: it owns the plan, ambiguity and review, and has a `delegate` tool.
            Haiku 5.5 is the SIDEKICK: one persistent session for the whole task (its context and prompt cache survive
            between delegations). The two exchange only briefs and reports, never transcripts. Lead and sidekick never
            act at the same time (one lock), so there is a single writer.

The design follows Cognition's description of Fusion (cognition.com/blog/local-fusion, chart updated 2026-10-19): a
frontier lead for planning and review, a cheaper sidekick that explores code, implements, runs tests and reports back.

Models run on the host through the Claude Agent SDK with every built-in tool disabled. Task tools (`bash`,
`view_image`) run inside the Daytona task container. No user/global Claude settings, no claude.ai connectors, no native
Claude Code advisor. The hidden tests and grader output are never shown to any model; there are no score-based retries.
"""
import asyncio
import base64
import json
import shlex
import shutil
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
                              ServerToolUseBlock, TextBlock, ToolUseBlock, create_sdk_mcp_server, tool)
from harbor.agents.base import BaseAgent

OPUS, HAIKU = 'claude-opus-5-5', 'claude-haiku-5-5'
EFFORT = 'high'                       # same for every session in every arm
COMMAND_TIMEOUT_SEC = 300             # per bash command, enforced inside the container
OUTPUT_CHARS = 30000                  # tail kept from one command's output (same as V64)
RUN_WALL_SEC = 120 * 60               # whole-run guard, same for every arm
DELEGATION_WALL_SEC = 30 * 60         # one delegation; the sidekick is interrupted after this
STOP_WAIT_SEC = 120                   # how long an interrupted session gets to deliver its final result
LEAD_BUDGET_USD = 15.0                # runaway guard only (same for OpusSolo and the Fusion lead)
SIDEKICK_BUDGET_USD = 3.0             # runaway guard only (same for HaikuSolo and the Fusion sidekick)

# Harness hygiene, carried over from the 2026-10-16 Aider Polyglot fix: without these the SDK's child CLI loads the
# account's claude.ai connectors (~28k tokens, attached asynchronously so they can also break the prompt cache mid-run)
# and names each session with an extra model call. MCP_TOOL_TIMEOUT keeps a long delegation from timing out the lead.
HARNESS_ENV = {'ENABLE_CLAUDEAI_MCP_SERVERS': 'false', 'CLAUDE_CODE_DISABLE_TERMINAL_TITLE': '1',
               'CLAUDE_CODE_DISABLE_ADVISOR_TOOL': '1', 'MCP_TOOL_TIMEOUT': str((DELEGATION_WALL_SEC + 300) * 1000)}

TOOLS_NOTE = """Your tools run inside the task's Linux container, working directory /app:
- `bash`: run one bash command (cwd /app, 300-second limit per command; split long jobs or run them in the background with output to a file). Files persist between calls; shell variables do not. Use here-documents to write files.
- `view_image`: look at an image file inside the container.
You have no other tools and no access to the host machine."""

BASE_PROMPT = """You are an expert engineer completing a task inside a Linux container.

""" + TOOLS_NOTE + """

The task's official tests are hidden and run after you finish; you may write and run your own checks. Nobody will answer questions, so resolve ambiguities yourself from the task text and the files. Keep going until the task is completely done, then finish with a short summary."""

LEAD_PROMPT = BASE_PROMPT + """

## You lead a two-model team
You are the LEAD. You own the plan, the interpretation of ambiguous or subtle requirements, and the review of all work. You also have a SIDEKICK: a fast model whose tokens cost about 1/40 of yours, with its own `bash` and `view_image` in the same container (same /app). The sidekick keeps its own memory across delegations, so follow-ups do not need to repeat context. It never sees your conversation.

Use `delegate` for concrete, well-scoped work, especially work that means reading a lot of output or many trial-and-error steps:
- exploring the files, code or data and summarizing what matters
- installing dependencies, running programs and test suites, iterating on failures
- implementing changes you have specified
- writing and running checks you describe
Keep for yourself: understanding the task, decisions where the requirements are ambiguous or subtle, the design of tricky logic, and review. If the sidekick fails at the same thing twice, or the result hinges on a subtle requirement, do that part yourself. Do not delegate just to delegate.

Write each brief so it stands alone: the goal, the exact requirements that matter (quote them), constraints (which files it may change, what must not change), how to verify, and what to report.

Review what comes back. A delegate result has the sidekick's report plus facts recorded by the harness (commands it ran with exit codes, files it modified). Check important claims with cheap targeted reads or commands before relying on them. Only you decide when the task is done: before finishing, make sure every requirement in the task is met."""

SIDEKICK_PROMPT = """You are the SIDEKICK in a two-model engineering team working on a task inside a Linux container. A lead engineer sends you briefs; do what each brief asks. You keep your memory across briefs.

""" + TOOLS_NOTE + """

- Stay within the brief. Do not make changes it did not ask for. If the brief is ambiguous, or you find something that contradicts it, say so in your report instead of guessing silently.
- Verify your work by actually running it where possible.
- End every brief with this report (under about 400 words; quote key numbers and error lines, not long logs):
STATUS: done | partial | blocked
DONE: what you did
CHANGED FILES: paths
VERIFICATION: commands you ran and their outcomes
ISSUES: problems, doubts, or anything the lead should check"""

FIRST_BRIEF = """BACKGROUND: the team's overall task, for context only. Do only what the brief below asks.
<task>
{task}
</task>

BRIEF FROM THE LEAD:
{brief}"""

ADVISOR_NOTE = """

You have an `advisor` tool backed by a stronger model that sees this conversation. Call it after reading the task and before writing code, when stuck, and once before finishing."""

DELEGATE_DESCRIPTION = ('Hand a self-contained brief to your sidekick and wait for its report. The sidekick works in the same '
                        'container and remembers earlier briefs. Returns its report plus harness-recorded facts.')


def tail(text, limit):
    return text if len(text) <= limit else f'[{len(text) - limit} earlier chars omitted]\n' + text[-limit:]


def wrapped(command):
    """bash command run inside the container under a hard deadline (process group killed after it)."""
    return ('timeout --signal=TERM --kill-after=5 ' + str(COMMAND_TIMEOUT_SEC) + ' bash -c ' + shlex.quote(command)
            + '; rc=$?; if [ $rc -eq 124 ]; then echo "[harness] command deadline exceeded (' + str(COMMAND_TIMEOUT_SEC)
            + ' seconds); stopped." >&2; fi; exit $rc')


class EventLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.write_text('')
        self.commands = []                       # [(command, exit_code)] in order, for delegation facts

    def write(self, **row):
        row['t'] = round(time.time(), 3)
        with self.path.open('a') as f:
            f.write(json.dumps(row) + '\n')


class Container:
    """Task tools bound to one Harbor environment."""

    def __init__(self, environment, scratch):
        self.environment = environment
        self.scratch = Path(scratch)

    async def run(self, command):
        r = await self.environment.exec(command=wrapped(command), cwd='/app', timeout_sec=COMMAND_TIMEOUT_SEC + 60)
        out = (r.stdout or '') + (('\n[stderr]\n' + r.stderr) if r.stderr else '')
        return r.return_code, out

    async def plain(self, command, timeout=60):
        r = await self.environment.exec(command='bash -c ' + shlex.quote(command), cwd='/app', timeout_sec=timeout)
        return r.return_code, r.stdout or ''

    async def image(self, path):
        local = self.scratch / (uuid.uuid4().hex + path.suffix.lower())
        await self.environment.download_file(str(path), local)
        return local.read_bytes()


IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}


def task_tools(container, log, lock=None):
    async def guarded(fn):
        if lock is None:
            return await fn()
        async with lock:
            return await fn()

    @tool('bash', 'Run one bash command inside the task container (cwd /app, 300-second limit).', {'command': str})
    async def bash(args):
        command = args['command']

        async def go():
            try:
                code, out = await container.run(command)
            except Exception as exc:  # transport failure is reported, never fabricated as output
                log.write(kind='command', command=command, exit=None, error=type(exc).__name__)
                log.commands.append((command, None))
                return {'content': [{'type': 'text', 'text': f'Command relay failed ({type(exc).__name__}); its effect is unknown.'}],
                        'is_error': True}
            log.write(kind='command', command=command, exit=code, output=tail(out, 4000))
            log.commands.append((command, code))
            return {'content': [{'type': 'text', 'text': f'exit code {code}\n{tail(out, OUTPUT_CHARS)}'}]}
        return await guarded(go)

    @tool('view_image', 'View an image file inside the task container, e.g. /app/drawing.png.', {'path': str})
    async def view_image(args):
        path = PurePosixPath(args['path'] if args['path'].startswith('/') else '/app/' + args['path'])
        mime = IMAGE_TYPES.get(path.suffix.lower())
        if mime is None:
            return {'content': [{'type': 'text', 'text': 'Unsupported image type.'}], 'is_error': True}

        async def go():
            try:
                data = await container.image(path)
            except Exception as exc:
                return {'content': [{'type': 'text', 'text': f'Cannot read {path}: {type(exc).__name__}'}], 'is_error': True}
            log.write(kind='view_image', path=str(path), bytes=len(data))
            return {'content': [{'type': 'image', 'data': base64.b64encode(data).decode(), 'mimeType': mime}]}
        return await guarded(go)

    return [bash, view_image]


def options_for(model, system_prompt, tools, host_cwd, budget, advisor_model=None):
    server = create_sdk_mcp_server('task', tools=tools)
    env = dict(HARNESS_ENV)
    if advisor_model:   # Claude Code's own advisor tool (Anthropic's advisor strategy), enabled only for that arm
        env.pop('CLAUDE_CODE_DISABLE_ADVISOR_TOOL')
    return ClaudeAgentOptions(model=model, tools=[], mcp_servers={'task': server}, strict_mcp_config=True,
                              allowed_tools=['mcp__task__' + t.name for t in tools], setting_sources=[],
                              cwd=str(host_cwd), permission_mode='bypassPermissions', env=env,
                              settings=json.dumps({'advisorModel': advisor_model}) if advisor_model else None,
                              system_prompt=system_prompt, thinking={'type': 'adaptive'}, effort=EFFORT,
                              max_budget_usd=budget)


def result_row(m):
    return {'subtype': m.subtype, 'is_error': m.is_error, 'num_turns': m.num_turns, 'total_cost_usd': m.total_cost_usd,
            'model_usage': m.model_usage, 'usage': m.usage,
            'terminal_reason': getattr(m, 'terminal_reason', None), 'stop_reason': getattr(m, 'stop_reason', None)}


async def drain(client, log, role):
    """Consume one query's messages. Returns (texts, result_row or None)."""
    texts, result = [], None
    async for m in client.receive_response():
        if isinstance(m, AssistantMessage):
            for b in m.content:
                if isinstance(b, TextBlock) and b.text.strip():
                    texts.append(b.text)
                    log.write(kind='text', role=role, text=b.text)
                elif isinstance(b, ToolUseBlock):
                    log.write(kind='tool_use', role=role, name=b.name, input=b.input if b.name.endswith('delegate') else None)
                elif isinstance(b, ServerToolUseBlock):
                    log.write(kind='server_tool_use', role=role, name=b.name)
        elif isinstance(m, ResultMessage):
            result = result_row(m)
            log.write(kind='result', role=role, **{k: v for k, v in result.items() if k != 'model_usage'})
    return texts, result


async def run_query(client, log, role):
    """Drain the run's single query under the whole-run guard. Returns (result or None, outcome)."""
    try:
        _, result = await asyncio.wait_for(drain(client, log, role), RUN_WALL_SEC)
        return result, 'completed'
    except asyncio.TimeoutError:
        pass
    await client.interrupt()
    try:
        _, result = await asyncio.wait_for(drain(client, log, role), STOP_WAIT_SEC)
        return result, 'run_wall_clock_guard'
    except asyncio.TimeoutError:
        log.write(kind='stop_timeout', role=role)
        return None, 'run_wall_clock_guard_unstopped'


def session_summary(model, results):
    """SDK session cost is cumulative: the last result carries the whole session."""
    last = results[-1] if results else {}
    return {'model': model, 'cost_usd': last.get('total_cost_usd'), 'num_turns': sum(r.get('num_turns') or 0 for r in results),
            'queries': len(results), 'results': results,
            'models_in_session': sorted({k for r in results for k in (r.get('model_usage') or {})}),
            'per_model_cost_usd': {k: v.get('costUSD') for k, v in (last.get('model_usage') or {}).items()}}


def delegation_report(n, texts, result, commands, changed, interrupted, error=None):
    report = texts[-1] if texts else '(the sidekick returned no report)'
    failed = [c for c in commands if c[1] not in (0,)]
    lines = [f'SIDEKICK REPORT (delegation #{n}):', report.strip(), '',
             'HARNESS FACTS (recorded by the harness, not written by the sidekick):',
             f'- commands run: {len(commands)} ({len(failed)} with nonzero or unknown exit)']
    for command, code in commands[-12:]:
        one = ' '.join(command.split())
        lines.append(f'  [exit {code}] {one[:200]}' + ('...' if len(one) > 200 else ''))
    if changed is None:
        lines.append('- files modified under /app: unknown (listing failed)')
    else:
        lines.append(f'- files modified under /app during this delegation: {len(changed)}')
        lines += [f'  {p}' for p in changed[:40]] + (['  ...'] if len(changed) > 40 else [])
    if result is None:
        lines.append('- the sidekick session ended without a result message')
    elif result.get('subtype') != 'success':
        lines.append(f"- sidekick session result: {result.get('subtype')}")
    if interrupted:
        lines.append(f'- the harness interrupted this delegation after {DELEGATION_WALL_SEC // 60} minutes')
    if error:
        lines.append(f'- the sidekick session failed ({error}); the next delegation starts a fresh sidekick session '
                     'that remembers nothing from earlier briefs')
    return '\n'.join(lines)


CHANGED_FILES = ("find /app -newer {mark} -type f -not -path '*/.git/*' -not -path '*/node_modules/*' "
                 "-not -path '*/__pycache__/*' -not -path '*/.venv/*' 2>/dev/null | sort | head -200")


class Sidekick:
    """One persistent Haiku session for the whole run; each delegation is one more query in it.

    If the session breaks (CLI crash, a stop that never completes), it is dropped and the next delegation opens a
    fresh one, which gets the task text again. Each session's cost is its last cumulative SDK result; a session that
    was used but never produced a result makes the sidekick cost unknown rather than zero.
    """

    def __init__(self, container, log, task, host_cwd, model=HAIKU):
        self.container, self.log, self.task, self.model = container, log, task, model
        self.tools = task_tools(container, log)
        self.options = options_for(model, SIDEKICK_PROMPT, self.tools, host_cwd, SIDEKICK_BUDGET_USD)
        self.client = None
        self.sessions, self.delegations = [], []   # sessions: [{'results': [...], 'attempts': n}]
        self.attempts = 0
        self.token = uuid.uuid4().hex[:12]

    @property
    def results(self):
        return [r for s in self.sessions for r in s['results']]

    async def _drop_client(self):
        client, self.client = self.client, None
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), STOP_WAIT_SEC)
            except Exception:
                pass

    async def delegate(self, brief):
        self.attempts += 1
        n = self.attempts
        started, first_command = time.time(), len(self.log.commands)
        mark = f'/tmp/.fusion-{self.token}-{n}'
        await self.container.plain(f'touch {mark}')
        fresh = self.client is None
        prompt = FIRST_BRIEF.format(task=self.task, brief=brief) if fresh else brief
        self.log.write(kind='brief', n=n, chars=len(brief), text=brief, fresh_session=fresh)
        if fresh:
            self.sessions.append({'results': [], 'attempts': 0})
        session = self.sessions[-1]
        session['attempts'] += 1
        before = session['results'][-1].get('total_cost_usd') if session['results'] else 0.0
        texts, result, interrupted, error = [], None, False, None
        try:
            if fresh:
                self.client = ClaudeSDKClient(self.options)
                await self.client.connect()
            await self.client.query(prompt)
            try:
                texts, result = await asyncio.wait_for(drain(self.client, self.log, 'sidekick'), DELEGATION_WALL_SEC)
            except asyncio.TimeoutError:
                interrupted = True
                await self.client.interrupt()
                texts, result = await asyncio.wait_for(drain(self.client, self.log, 'sidekick'), STOP_WAIT_SEC)
        except Exception as exc:
            error = type(exc).__name__
            self.log.write(kind='sidekick_error', n=n, error=repr(exc))
            await self._drop_client()
        if result is not None:
            session['results'].append(result)
        try:
            code, out = await self.container.plain(CHANGED_FILES.format(mark=mark))
            changed = [line for line in out.splitlines() if line.strip()] if code == 0 else None
        except Exception:
            changed = None
        commands = self.log.commands[first_command:]
        after = result.get('total_cost_usd') if result else None
        self.delegations.append({'n': n, 'fresh_session': fresh, 'brief_chars': len(brief),
                                 'seconds': round(time.time() - started, 1), 'commands': len(commands),
                                 'changed_files': None if changed is None else len(changed),
                                 'turns': result.get('num_turns') if result else None,
                                 'subtype': result.get('subtype') if result else None, 'interrupted': interrupted,
                                 'error': error, 'session_cost_after': after,
                                 # since the session's last known cumulative cost (covers an earlier unknown one)
                                 'cost_usd': (after - before) if isinstance(after, (int, float)) and isinstance(before, (int, float)) else None})
        report = delegation_report(n, texts, result, commands, changed, interrupted, error)
        self.log.write(kind='report', n=n, text=report)
        return report

    async def close(self):
        await self._drop_client()

    def summary(self):
        results = self.results
        costs = [s['results'][-1].get('total_cost_usd') if s['results'] else None for s in self.sessions]
        cost = sum(costs) if costs and all(isinstance(c, (int, float)) for c in costs) else None
        per_model = {}
        for s in self.sessions:
            for k, v in ((s['results'][-1].get('model_usage') or {}) if s['results'] else {}).items():
                if isinstance(v.get('costUSD'), (int, float)):
                    per_model[k] = per_model.get(k, 0.0) + v['costUSD']
        return {'model': self.model, 'cost_usd': cost, 'num_turns': sum(r.get('num_turns') or 0 for r in results),
                'queries': len(results), 'sessions': len(self.sessions), 'attempts': self.attempts, 'results': results,
                'models_in_session': sorted({k for r in results for k in (r.get('model_usage') or {})}),
                'per_model_cost_usd': per_model, 'delegations': self.delegations}


class Arm(BaseAgent):
    ARM = 'base'

    @staticmethod
    def name():
        return 'fusion-tb4-arm'

    def version(self):
        return '1'

    async def setup(self, environment):
        r = await environment.exec(command='command -v timeout >/dev/null && test -d /app', cwd='/', timeout_sec=60)
        if r.return_code:
            raise RuntimeError('Task container lacks coreutils timeout or /app')

    def populate_context_post_run(self, context):
        usage = self.logs_dir / 'usage.json'
        if usage.exists():
            context.cost_usd = json.loads(usage.read_text()).get('total_cost_usd')

    def _write_usage(self, sessions, started, outcome):
        costs = [s['cost_usd'] for s in sessions.values() if s is not None]
        complete = bool(costs) and all(isinstance(c, (int, float)) for c in costs)
        (self.logs_dir / 'usage.json').write_text(json.dumps({
            'arm': self.ARM, 'effort': EFFORT, 'sessions': sessions, 'outcome': outcome,
            'total_cost_usd': sum(costs) if complete else None, 'usage_complete': complete,
            'wall_seconds': round(time.time() - started, 1), 'verifier_feedback': False,
            'harness_env': HARNESS_ENV,
            'cost_basis': 'Claude Agent SDK total_cost_usd (list-price equivalent), not invoice; infrastructure excluded'},
            indent=2, default=str) + '\n')

    async def _solo(self, model, budget, instruction, environment, prompt=BASE_PROMPT, advisor_model=None):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix='fusion-tb4-'))
        host_cwd = scratch / 'host-cwd'
        host_cwd.mkdir()
        log = EventLog(self.logs_dir / 'events.jsonl')
        container = Container(environment, scratch)
        options = options_for(model, prompt, task_tools(container, log), host_cwd, budget, advisor_model)
        started, results, outcome = time.time(), [], 'error'
        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(instruction)
                result, outcome = await run_query(client, log, 'solo')
                if result is not None:
                    results.append(result)
        finally:
            self._write_usage({'solo': session_summary(model, results)}, started, outcome)
            shutil.rmtree(scratch, ignore_errors=True)


class OpusSolo(Arm):
    ARM = 'opus_solo'

    async def run(self, instruction, environment, context):
        await self._solo(OPUS, LEAD_BUDGET_USD, instruction, environment)


class HaikuSolo(Arm):
    ARM = 'haiku_solo'

    async def run(self, instruction, environment, context):
        await self._solo(HAIKU, SIDEKICK_BUDGET_USD, instruction, environment)


class HaikuAdvisor(Arm):
    ARM = 'haiku_native_opus_advisor'

    async def run(self, instruction, environment, context):
        await self._solo(HAIKU, LEAD_BUDGET_USD, instruction, environment, BASE_PROMPT + ADVISOR_NOTE, OPUS)


class Fusion(Arm):
    ARM = 'fusion_opus_lead_haiku_sidekick'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix='fusion-tb4-'))
        (scratch / 'lead-cwd').mkdir()
        (scratch / 'sidekick-cwd').mkdir()
        lead_log = EventLog(self.logs_dir / 'lead-events.jsonl')
        side_log = EventLog(self.logs_dir / 'sidekick-events.jsonl')
        container = Container(environment, scratch)
        sidekick = Sidekick(container, side_log, instruction, scratch / 'sidekick-cwd')
        lock = asyncio.Lock()

        @tool('delegate', DELEGATE_DESCRIPTION, {'brief': str})
        async def delegate(args):
            brief = (args.get('brief') or '').strip()
            if not brief:
                return {'content': [{'type': 'text', 'text': 'Empty brief; nothing was delegated.'}], 'is_error': True}
            async with lock:
                try:
                    report = await sidekick.delegate(brief)
                except Exception as exc:
                    lead_log.write(kind='delegate_error', error=repr(exc))
                    return {'content': [{'type': 'text', 'text': f'Delegation failed ({type(exc).__name__}); state of its work is unknown.'}],
                            'is_error': True}
            return {'content': [{'type': 'text', 'text': report}]}

        tools = task_tools(container, lead_log, lock) + [delegate]
        options = options_for(OPUS, LEAD_PROMPT, tools, scratch / 'lead-cwd', LEAD_BUDGET_USD)
        started, results, outcome = time.time(), [], 'error'
        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(instruction)
                result, outcome = await run_query(client, lead_log, 'lead')
                if result is not None:
                    results.append(result)
        finally:
            await sidekick.close()
            self._write_usage({'lead': session_summary(OPUS, results), 'sidekick': sidekick.summary() if sidekick.attempts else None},
                              started, outcome)
            shutil.rmtree(scratch, ignore_errors=True)
