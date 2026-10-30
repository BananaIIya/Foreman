"""Fusion with a GPT-6.1 Sol lead (native Codex) and a Claude Haiku 5.5 sidekick.

SolFusion   GPT-6.1 Sol (high) is the LEAD for the whole task in the native Codex app-server: its own shell and file
            tools run inside the Daytona task container through the official Codex exec-server (SSH-forwarded, the
            same transport as the earlier Sol/Luna runs in advisor-five-optimization). It also has a `delegate`
            dynamic tool. The SIDEKICK is the same persistent Haiku 5.5 session as in fusion_arms.Fusion (Claude Agent
            SDK, `bash` + `view_image` in the same container). The two exchange only briefs and reports.

Model traffic stays on the host. The hidden tests and grader output never reach either model; no score-based retries.
The Codex login is copied into a private CODEX_HOME for the run; if Codex refreshes it there, the refreshed file is
written back to ~/.codex/auth.json (only when that file is unchanged since the copy), so the user's login stays valid.
"""
import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import socket
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from harbor.environments.daytona.ssh import ssh_destination

from fusion_arms import Arm, Container, EventLog, Sidekick, HAIKU, RUN_WALL_SEC, STOP_WAIT_SEC

ROOT = Path(__file__).resolve().parent
CODEX = '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
CODEX_VERSION = 'codex-cli 0.163.0-alpha.10.2'
CODEX_LINUX = ROOT / 'runtime/codex-linux-0.163.0-alpha.10.2.tgz'   # codex + rg from @openai/codex@0.163.0-alpha.10.2-linux-x64
LEAD_MODEL, LEAD_EFFORT = 'gpt-6.1-sol', 'high'
EXEC_PORT = 38761
USER_AUTH = Path.home() / '.codex/auth.json'
# The SSH forward's local port opens before the remote service exists, so wait for the exec-server inside the container.
EXEC_SERVER_READY = ("bash -c 'for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/" + str(EXEC_PORT) + ") 2>/dev/null "
                     "&& exit 0; sleep 0.2; done; tail -c 400 /tmp/fusion-exec-server.log; exit 1'")

# USD per 1M tokens: input, cached input, cache write, output (same table as advisor-five-optimization/routing.py).
PRICES = {'gpt-6.1-sol': (Decimal('2'), Decimal('.10'), Decimal('2.50'), Decimal('10')),
          'gpt-6-luna': (Decimal('.10'), Decimal('.01'), Decimal('.125'), Decimal('.50'))}

SOL_LEAD = """You are an expert engineer completing a task in a Linux container (working directory /app). Your shell and file tools run inside that container. The task's official tests are hidden and run after you finish; you may write and run your own checks. Nobody will answer questions, so resolve ambiguities yourself from the task text and the files. Keep going until the task is completely done, then finish with a short summary.

## You lead a two-model team
You are the LEAD. You own the plan, the interpretation of ambiguous or subtle requirements, and the review of all work. You also have a SIDEKICK: a fast model whose tokens cost about 1/20 of yours, with its own `bash` and `view_image` in the same container (same /app). The sidekick keeps its own memory across delegations, so follow-ups do not need to repeat context. It never sees your conversation.

Use the `delegate` tool for concrete, well-scoped work, especially work that means reading a lot of output or many trial-and-error steps:
- exploring the files, code or data and summarizing what matters
- installing dependencies, running programs and test suites, iterating on failures
- implementing changes you have specified
- writing and running checks you describe
Keep for yourself: understanding the task, decisions where the requirements are ambiguous or subtle, the design of tricky logic, and review. If the sidekick fails at the same thing twice, or the result hinges on a subtle requirement, do that part yourself. Do not delegate just to delegate. While a delegation runs, wait for its result; do not run commands at the same time.

Write each brief so it stands alone: the goal, the exact requirements that matter (quote them), constraints (which files it may change, what must not change), how to verify, and what to report.

Review what comes back. A delegate result has the sidekick's report plus facts recorded by the harness (commands it ran with exit codes, files it modified). Check important claims with cheap targeted reads or commands before relying on them. Only you decide when the task is done: before finishing, make sure every requirement in the task is met."""

DELEGATE_TOOL = {
    'type': 'function', 'name': 'delegate',
    'description': ('Hand a self-contained brief to your sidekick and wait for its report. The sidekick works in the same '
                    'container and remembers earlier briefs. Returns its report plus harness-recorded facts.'),
    'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['brief'],
                    'properties': {'brief': {'type': 'string', 'maxLength': 20000,
                                             'description': 'Goal, exact requirements, constraints, how to verify, what to report.'}}},
}


def usage_cost(model, usage):
    """Codex counters are inclusive: inputTokens includes cached reads and cache writes. Reasoning is inside output."""
    keys = ['inputTokens', 'cachedInputTokens', 'cacheWriteInputTokens', 'outputTokens']
    if not all(isinstance(usage.get(k), (int, float)) and usage[k] >= 0 for k in keys):
        return None
    inp, cached, writes, out = [Decimal(str(usage[k])) for k in keys]
    fresh = inp - cached - writes
    if fresh < 0:
        raise ValueError('Inconsistent inclusive input counters')
    p_in, p_cache, p_write, p_out = PRICES[model]
    if inp > 272000:   # long-context request pricing
        p_in, p_cache, p_write, p_out = p_in * 2, p_cache * 2, p_write * 2, p_out * Decimal('1.5')
    return float((fresh * p_in + cached * p_cache + writes * p_write + out * p_out) / 1000000)


def account_codex(events, model):
    """One priced row per model response, deduplicated on the cumulative total."""
    seen, rows = set(), []
    for e in events:
        if e.get('method') != 'thread/tokenUsage/updated':
            continue
        p = e['params']
        u = p['tokenUsage']
        key = (p['threadId'], p.get('turnId'), json.dumps(u['total'], sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        rows.append({'usage': u['last'], 'cost_usd': usage_cost(model, u['last'])})
    costs = [r['cost_usd'] for r in rows]
    totals = {k: sum(r['usage'].get(k) or 0 for r in rows)
              for k in ('inputTokens', 'cachedInputTokens', 'cacheWriteInputTokens', 'outputTokens', 'reasoningOutputTokens')}
    return {'responses': len(rows), 'tokens': totals,
            'cost_usd': sum(costs) if costs and all(c is not None for c in costs) else None}


def read_events(path):
    rows = []
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            with contextlib.suppress(ValueError):
                rows.append(json.loads(line))
    return rows


class CodexLead:
    """Native Codex app-server over stdio; answers only `delegate` dynamic-tool calls, refuses any other request."""

    def __init__(self, home, log_dir, tool_handler, model=LEAD_MODEL, effort=LEAD_EFFORT):
        self.home, self.log_dir, self.tool_handler = Path(home), Path(log_dir), tool_handler
        self.model, self.effort = model, effort
        self.pending, self.notifications, self.counter = {}, asyncio.Queue(), 0
        self.tool_tasks = set()

    async def start(self):
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'TERM')}
        env['CODEX_HOME'] = str(self.home)
        self.stderr = (self.log_dir / 'app-server.stderr.log').open('w')
        self.events = (self.log_dir / 'app-server.events.jsonl').open('w')
        self.proc = await asyncio.create_subprocess_exec(CODEX, 'app-server', '--stdio', cwd=str(self.home), env=env,
                                                         stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                         stderr=self.stderr, limit=32 * 1024 * 1024)
        self.reader = asyncio.create_task(self.read())
        await self.rpc('initialize', {'clientInfo': {'name': 'fusion_sol_lead', 'title': 'Fusion Sol lead', 'version': '1.0'},
                                      'capabilities': {'experimentalApi': True}})
        await self.send({'method': 'initialized'})

    async def send(self, message):
        self.proc.stdin.write((json.dumps(message) + '\n').encode())
        await self.proc.stdin.drain()

    async def rpc(self, method, params, timeout=90):
        self.counter += 1
        rid = self.counter
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self.send({'id': rid, 'method': method, 'params': params})
        return await asyncio.wait_for(fut, timeout)

    async def read(self):
        try:
            while line := await self.proc.stdout.readline():
                try:
                    message = json.loads(line)
                except ValueError:   # never let one malformed line kill the transport
                    self.events.write(json.dumps({'unparsed_line': line.decode(errors='replace')[:2000]}) + '\n')
                    continue
                self.events.write(json.dumps(message) + '\n')
                self.events.flush()
                if 'id' in message and 'method' not in message:
                    fut = self.pending.pop(message['id'], None)
                    if fut and not fut.done():
                        if 'error' in message:
                            fut.set_exception(RuntimeError(json.dumps(message['error'])))
                        else:
                            fut.set_result(message.get('result'))
                elif 'id' in message and message.get('method') == 'item/tool/call':
                    task = asyncio.create_task(self._answer_tool(message))   # slow; keep events flowing
                    self.tool_tasks.add(task)
                    task.add_done_callback(self.tool_tasks.discard)
                elif 'id' in message:
                    # Unattended run: never silently answer an approval or clarification request.
                    await self.send({'id': message['id'], 'error': {'code': -32603, 'message': 'No interactive handler in benchmark'}})
                    await self.notifications.put({'method': 'transport/unexpected_request', 'params': {'method': message['method']}})
                else:
                    await self.notifications.put(message)
        finally:
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError('Codex app-server closed'))
            await self.notifications.put({'method': 'transport/closed'})

    async def _answer_tool(self, message):
        params = message['params']
        try:
            result = await self.tool_handler(params)
        except Exception as exc:   # the lead sees a failed call, never a fabricated report
            result = {'success': False, 'contentItems': [{'type': 'inputText', 'text': f'Delegation failed ({type(exc).__name__}); state of its work is unknown.'}]}
        await self.send({'id': message['id'], 'result': result})

    async def run(self, url, instruction, cwd, guard=None, instructions=SOL_LEAD):
        """url=None is only for the local protocol probe: no remote executor, read-only sandbox, guard required."""
        if url is None and guard is None:
            raise ValueError('local runs need a guard that stops any tool other than delegate')
        environments = []
        if url is not None:
            await self.rpc('environment/add', {'environmentId': 'daytona', 'execServerUrl': url, 'connectTimeoutMs': 30000})
            environments = [{'environmentId': 'daytona', 'cwd': cwd, 'runtimeWorkspaceRoots': [cwd]}]
        thread = await self.rpc('thread/start', {
            'model': self.model, 'allowProviderModelFallback': False, 'approvalPolicy': 'never',
            'sandbox': 'danger-full-access' if url else 'read-only', 'cwd': cwd, 'environments': environments,
            'config': {'model_reasoning_effort': self.effort, 'service_tier': 'default',
                       'features': {'unified_exec': True, 'multi_agent': False}, 'web_search': 'disabled'},
            'developerInstructions': instructions, 'dynamicTools': [DELEGATE_TOOL], 'experimentalRawEvents': True})
        if thread.get('model') != self.model or thread.get('reasoningEffort') != self.effort:
            raise RuntimeError('Requested model/effort not selected: ' + str({k: thread.get(k) for k in ('model', 'reasoningEffort')}))
        thread_id = thread['thread']['id']
        started = await self.rpc('turn/start', {'threadId': thread_id, 'effort': self.effort, 'environments': environments,
                                                'input': [{'type': 'text', 'text': instruction, 'text_elements': []}]})
        turn_id = started['turn']['id']
        deadline, interrupted, stopping = time.monotonic() + RUN_WALL_SEC, None, False
        while True:
            try:
                event = await asyncio.wait_for(self.notifications.get(), max(1.0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                if stopping:
                    raise RuntimeError('Codex turn did not finish after interrupt')
                interrupted = interrupted or 'run_wall_clock_guard'
                await self.interrupt(thread_id, turn_id)
                stopping, deadline = True, time.monotonic() + STOP_WAIT_SEC
                continue
            if event.get('method', '').startswith('transport/'):
                raise RuntimeError(str(event))
            if guard is not None and interrupted is None:
                reason = guard(event)
                if reason:
                    interrupted = reason
                    await self.interrupt(thread_id, turn_id)
                    stopping, deadline = True, time.monotonic() + STOP_WAIT_SEC
            if event.get('method') == 'turn/completed' and event['params']['turn']['id'] == turn_id:
                return {'thread_id': thread_id, 'turn_id': turn_id, 'status': event['params']['turn'].get('status'),
                        'interrupted': interrupted}

    async def interrupt(self, thread_id, turn_id):
        try:
            await self.rpc('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
        except (RuntimeError, asyncio.TimeoutError):
            pass   # the turn can finish on its own while the request is in flight; its completed event still counts

    async def close(self):
        """Never raises: callers run credential and sandbox cleanup after it."""
        for task in list(self.tool_tasks):
            task.cancel()
        if hasattr(self, 'proc'):
            with contextlib.suppress(Exception):
                if self.proc.returncode is None:
                    self.proc.terminate()
                    try:
                        await asyncio.wait_for(self.proc.wait(), 10)
                    except asyncio.TimeoutError:
                        self.proc.kill()
                        await self.proc.wait()
            with contextlib.suppress(BaseException):
                await self.reader
            self.events.close()
            self.stderr.close()


def private_codex_home(private):
    home = Path(private) / 'codex-home'
    home.mkdir(mode=0o700)
    original = USER_AUTH.read_bytes()
    (home / 'auth.json').write_bytes(original)
    (home / 'auth.json').chmod(0o600)
    return home, hashlib.sha256(original).hexdigest()


def return_refreshed_login(home, original_sha):
    """If Codex refreshed the login inside the private home, keep the user's own login file current."""
    copy = Path(home) / 'auth.json'
    if not copy.exists():
        return 'no private login file'
    new = copy.read_bytes()
    if hashlib.sha256(new).hexdigest() == original_sha:
        return 'unchanged'
    if hashlib.sha256(USER_AUTH.read_bytes()).hexdigest() != original_sha:
        return 'refreshed in run, but ~/.codex/auth.json changed meanwhile; left as is'
    tmp = USER_AUTH.with_name('.auth.json.fusion-tmp')
    tmp.write_bytes(new)
    tmp.chmod(0o600)
    os.replace(tmp, USER_AUTH)
    return 'refreshed in run; written back to ~/.codex/auth.json'


async def install_codex(environment):
    """Official Linux codex binary (exact host version) for the exec-server; no other runtime changes."""
    await environment.upload_file(CODEX_LINUX, '/tmp/fusion-codex.tgz')
    r = await environment.exec(command=('mkdir -p /usr/local/bin && tar -xzf /tmp/fusion-codex.tgz -C /usr/local/bin codex rg '
                                        '&& chmod 755 /usr/local/bin/codex /usr/local/bin/rg && rm /tmp/fusion-codex.tgz '
                                        '&& /usr/local/bin/codex --version'), cwd='/', timeout_sec=300, user='root')
    if r.return_code or (r.stdout or '').strip() != CODEX_VERSION:
        raise RuntimeError('Codex install failed: ' + (r.stdout or '')[-300:] + (r.stderr or '')[-300:])


class SolFusion(Arm):
    ARM = 'fusion_sol_lead_haiku_sidekick'

    async def setup(self, environment):
        await super().setup(environment)
        await install_codex(environment)

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        sandbox = environment._sandbox
        if sandbox is None:
            raise RuntimeError('No Daytona sandbox')
        check = await asyncio.create_subprocess_exec(CODEX, '--version', stdout=asyncio.subprocess.PIPE)
        if (await check.communicate())[0].decode().strip() != CODEX_VERSION:
            raise RuntimeError('Host Codex version drift')
        private = Path(tempfile.mkdtemp(prefix='fusion-sol-', dir='/private/tmp'))
        try:   # the private dir holds a copy of the Codex login: it is removed whatever happens below
            await self._run(instruction, environment, sandbox, private)
        finally:
            shutil.rmtree(private, ignore_errors=True)

    async def _run(self, instruction, environment, sandbox, private):
        (private / 'sidekick-cwd').mkdir()
        home, auth_sha = private_codex_home(private)
        side_log = EventLog(self.logs_dir / 'sidekick-events.jsonl')
        delegate_log = EventLog(self.logs_dir / 'delegate-calls.jsonl')
        sidekick = Sidekick(Container(environment, private), side_log, instruction, private / 'sidekick-cwd', model=HAIKU)
        lock = asyncio.Lock()

        async def handle(params):
            args = params.get('arguments') or {}
            if isinstance(args, str):
                args = json.loads(args or '{}')
            if params.get('tool') != 'delegate':
                return {'success': False, 'contentItems': [{'type': 'inputText', 'text': 'Unknown tool.'}]}
            brief = (args.get('brief') or '').strip()
            if not brief:
                return {'success': False, 'contentItems': [{'type': 'inputText', 'text': 'Empty brief; nothing was delegated.'}]}
            delegate_log.write(kind='call', call_id=params.get('callId'), brief=brief)
            async with lock:
                report = await sidekick.delegate(brief)
            delegate_log.write(kind='returned', call_id=params.get('callId'), chars=len(report))
            return {'success': True, 'contentItems': [{'type': 'inputText', 'text': report}]}

        lead = CodexLead(home, self.logs_dir, handle)
        lease = tunnel = outcome = None
        ssh_log = (private / 'ssh.log').open('w')
        started = time.time()
        try:
            cwd_r = await environment.exec(command='pwd')
            cwd = (cwd_r.stdout or '').strip() or '/app'
            await environment.exec(command=(f'nohup /usr/local/bin/codex exec-server --listen ws://127.0.0.1:{EXEC_PORT} '
                                            '>/tmp/fusion-exec-server.log 2>&1 </dev/null & echo $! >/tmp/fusion-exec-server.pid'))
            ready = await environment.exec(command=EXEC_SERVER_READY, timeout_sec=60)
            if ready.return_code:
                raise RuntimeError('Codex exec-server did not start: ' + (ready.stdout or '')[-500:])
            lease = await sandbox.create_ssh_access(expires_in_minutes=240)
            host, port = ssh_destination(lease.ssh_command, lease.token)
            config = private / 'ssh-config'
            config.write_text(f'Host fusion-executor\n HostName {host}\n Port {port}\n User {lease.token}\n'
                              f' StrictHostKeyChecking accept-new\n UserKnownHostsFile {private}/known_hosts\n'
                              ' BatchMode yes\n ExitOnForwardFailure yes\n ServerAliveInterval 15\n ServerAliveCountMax 3\n')
            config.chmod(0o600)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                local_port = sock.getsockname()[1]
            tunnel = await asyncio.create_subprocess_exec('ssh', '-F', str(config), '-N', '-L',
                                                          f'127.0.0.1:{local_port}:127.0.0.1:{EXEC_PORT}', 'fusion-executor',
                                                          stdout=ssh_log, stderr=ssh_log)
            for _ in range(150):
                if tunnel.returncode is not None:
                    raise RuntimeError('SSH executor connection exited')
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', local_port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(.2)
            else:
                raise RuntimeError('SSH executor port not ready')
            await lead.start()
            outcome = await lead.run(f'ws://127.0.0.1:{local_port}', instruction, cwd)
            (self.logs_dir / 'native-outcome.json').write_text(json.dumps(outcome, indent=2) + '\n')
        finally:
            # Each step is independent: one failing must not skip the login write-back or the sandbox cleanup.
            await lead.close()
            with contextlib.suppress(Exception):
                await sidekick.close()
            login = 'not checked'
            with contextlib.suppress(Exception):
                login = return_refreshed_login(home, auth_sha)
            with contextlib.suppress(Exception):
                self._record(outcome, sidekick, started, login)
            with contextlib.suppress(Exception):
                if (home / 'sessions').exists():
                    shutil.copytree(home / 'sessions', self.logs_dir / 'codex-sessions', dirs_exist_ok=True)
            with contextlib.suppress(Exception):
                if tunnel is not None and tunnel.returncode is None:
                    tunnel.terminate()
                    try:
                        await asyncio.wait_for(tunnel.wait(), 10)
                    except asyncio.TimeoutError:
                        tunnel.kill()
                        await tunnel.wait()
            if lease is not None:
                with contextlib.suppress(Exception):
                    await sandbox.revoke_ssh_access(lease.token)
            ssh_log.close()

    def _record(self, outcome, sidekick, started, login):
        lead_acct = account_codex(read_events(self.logs_dir / 'app-server.events.jsonl'), LEAD_MODEL)
        sessions = {'lead': {'model': LEAD_MODEL, 'effort': LEAD_EFFORT, 'cost_usd': lead_acct['cost_usd'],
                             'responses': lead_acct['responses'], 'tokens': lead_acct['tokens'],
                             'num_turns': lead_acct['responses'], 'models_in_session': [LEAD_MODEL]},
                    'sidekick': sidekick.summary() if sidekick.attempts else None}
        if outcome is None:
            state = 'error'
        elif outcome.get('interrupted'):
            state = outcome['interrupted']
        else:
            state = 'completed' if outcome.get('status') == 'completed' else f"turn_{outcome.get('status')}"
        self._write_usage(sessions, started, state)
        usage = json.loads((self.logs_dir / 'usage.json').read_text())
        usage.update(codex_login=login, host_codex=CODEX_VERSION, lead_turn_status=(outcome or {}).get('status'))
        (self.logs_dir / 'usage.json').write_text(json.dumps(usage, indent=2, default=str) + '\n')
