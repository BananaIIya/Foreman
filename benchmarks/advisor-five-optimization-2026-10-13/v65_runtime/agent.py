"""V65 cheap-first, verify-first routing.

Stage 1  Haiku 5.5 attempts the whole task alone (V64 HaikuSolo setup: bash/view_image relayed into the
         Daytona container, built-in tools off, native Claude advisor off), with a hard budget cap.
Stage 2  Sol high opens a native session in the SAME workspace (V61 session: no early-stop framing, no
         mid-turn steer, hard budget cap). It first verifies Haiku's deliverable with real reads and checks;
         if it is correct it stops without edits, otherwise it fixes and finishes the task.
Workspace hashes before/after stage 2 record whether Sol changed anything.
No hidden tests, grader output or earlier solutions are given to any model.
"""
import asyncio
import base64
import contextlib
import hashlib
import json
import shutil
import socket
import tempfile
from pathlib import Path, PurePosixPath

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
                              ServerToolUseBlock, TextBlock, ToolUseBlock, create_sdk_mcp_server, tool)
from harbor.environments.daytona.ssh import ssh_destination
from native_remote import CODEX
from remote_native_codex import RemoteNativeCodex
from v60_runtime.remote import run_task_command
from v61_runtime.native import AdvisorSession as SolSession
from .transcript import completed_items, render

HAIKU = 'claude-haiku-5-5'
OUTPUT_CHARS = 30000
TRANSCRIPT_CHARS = 40000
SOL_SOLO_MEAN = {'cad-model': 0.184954, 'hof-topology-interpenetration': 0.32619346666666665,
                 'satb-audio-transcription': 0.49993213333333336}
# Haiku cap 40% / Sol cap 88% of the Sol solo mean; fixed caps where no Sol solo baseline exists.
HAIKU_CAP = {t: .40 * c for t, c in SOL_SOLO_MEAN.items()} | {'wal-recovery-ordering': .20, 'atrx-vep-crispr': .20}
SOL_CAP = {t: .88 * c for t, c in SOL_SOLO_MEAN.items()} | {'wal-recovery-ordering': .60, 'atrx-vep-crispr': .60}

TASK_TOOLS_NOTE = """Your tools run inside the task's Linux container, whose working directory is /app:
- `bash`: run one bash command (cwd /app, 300-second limit). Files persist between calls; shell variables do not. Use here-documents to write files.
- `view_image`: look at an image file inside the container (for example a drawing referenced by the task).
You have no other tools and no access to the host machine."""

VERIFY_FIRST = """A lower-cost model has already attempted this task in this same /app workspace; its transcript is summarized below.
You own the final result. FIRST verify its deliverable against the original task by reading the actual files and running real checks yourself.
- If the deliverable is correct and complete, stop and report it as verified. Do not edit anything.
- Change files only for a concrete failed check or a demonstrated violation of the task's requirements; then fix and finish the task completely.
Treat the earlier model's claims as unverified. Work as you would if solving the task on your own; do not stop early to save budget, but spend no more than a careful verification or completion needs. No hidden tests, grader output or other models are available."""

HASH_LIST = ("find /app -type f -not -path '*/__pycache__/*' -not -path '/app/.git/*' -size -50M -print0 2>/dev/null "
             "| sort -z | xargs -0 -r sha256sum")
SNAPSHOT_CMD = HASH_LIST + " > /tmp/v65-hash-before.txt; wc -l < /tmp/v65-hash-before.txt"
DIFF_CMD = (HASH_LIST + " > /tmp/v65-hash-after.txt; "
            "diff /tmp/v65-hash-before.txt /tmp/v65-hash-after.txt | grep -E '^[<>]' | sed -E 's/^[<>] [0-9a-f]+  //' | sort -u")


class CheapFirstVerify(RemoteNativeCodex):
    def populate_context_post_run(self, context):
        usage = self.logs_dir / 'v65-usage.json'
        if usage.exists():
            context.cost_usd = json.loads(usage.read_text()).get('total_cost_usd')

    async def _snapshot(self, environment):
        r = await run_task_command(self, environment, SNAPSHOT_CMD)
        return int((r.stdout or '0').strip() or 0) if r.return_code == 0 else None

    async def _changed(self, environment):
        r = await run_task_command(self, environment, DIFF_CMD)
        return [l for l in (r.stdout or '').splitlines() if l.strip()], bool(getattr(r, 'stdout_truncated', False))

    async def run(self, instruction, environment, context):
        if self.model_name != 'anthropic/' + HAIKU:
            raise RuntimeError('Unexpected benchmark model')
        task = environment.environment_dir.parent.name
        private = Path(tempfile.mkdtemp(prefix='v65-', dir='/private/tmp'))
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        events_path = self.logs_dir / 'executor-events.jsonl'
        events_path.write_text('')
        host_cwd = private / 'host-cwd'
        host_cwd.mkdir()
        record = {'task': task, 'haiku_cap_usd': HAIKU_CAP[task], 'sol_cap_usd': SOL_CAP[task]}

        def log_item(item):
            with events_path.open('a') as f:
                f.write(json.dumps({'method': 'item/completed', 'params': {'item': item}}) + '\n')

        # ---------- Stage 1: Haiku alone, capped ----------
        @tool('bash', 'Run one bash command inside the task container (cwd /app, 300-second limit).', {'command': str})
        async def bash(args):
            command = args['command']
            try:
                r = await run_task_command(self, environment, command)
            except Exception as exc:
                log_item({'type': 'commandExecution', 'command': command, 'exitCode': None, 'aggregatedOutput': f'relay error {type(exc).__name__}'})
                return {'content': [{'type': 'text', 'text': f'Command relay failed ({type(exc).__name__}); nothing is known about its effect.'}], 'is_error': True}
            output = (r.stdout or '') + (('\n[stderr]\n' + r.stderr) if r.stderr else '')
            log_item({'type': 'commandExecution', 'command': command, 'exitCode': r.return_code, 'aggregatedOutput': output})
            if len(output) > OUTPUT_CHARS:
                output = f'[{len(output) - OUTPUT_CHARS} earlier chars omitted]\n' + output[-OUTPUT_CHARS:]
            return {'content': [{'type': 'text', 'text': f'exit code {r.return_code}\n{output}'}]}

        @tool('view_image', 'View an image file inside the task container, e.g. /app/schematic.png.', {'path': str})
        async def view_image(args):
            path = PurePosixPath(args['path'] if args['path'].startswith('/') else '/app/' + args['path'])
            suffix = path.suffix.lower()
            mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}.get(suffix)
            if not mime:
                return {'content': [{'type': 'text', 'text': 'Unsupported image type.'}], 'is_error': True}
            local = private / ('image' + suffix)
            try:
                await environment.download_file(str(path), local)
            except Exception as exc:
                return {'content': [{'type': 'text', 'text': f'Cannot read {path}: {type(exc).__name__}'}], 'is_error': True}
            log_item({'type': 'commandExecution', 'command': f'view_image {path}', 'exitCode': 0, 'aggregatedOutput': '[image shown to executor]'})
            return {'content': [{'type': 'image', 'data': base64.b64encode(local.read_bytes()).decode(), 'mimeType': mime}]}

        server = create_sdk_mcp_server('task', tools=[bash, view_image])
        options = ClaudeAgentOptions(
            model=HAIKU, tools=[], mcp_servers={'task': server}, allowed_tools=['mcp__task__bash', 'mcp__task__view_image'],
            setting_sources=[], cwd=str(host_cwd), permission_mode='bypassPermissions', max_budget_usd=HAIKU_CAP[task],
            env={'CLAUDE_CODE_DISABLE_ADVISOR_TOOL': '1'},
            system_prompt={'type': 'preset', 'preset': 'claude_code', 'append': TASK_TOOLS_NOTE})
        haiku_results, server_tools = [], []

        async def haiku_stage():
            async with ClaudeSDKClient(options) as client:
                await client.query(instruction)
                async for m in client.receive_response():
                    if isinstance(m, AssistantMessage):
                        for b in m.content:
                            if isinstance(b, TextBlock) and b.text.strip():
                                log_item({'type': 'agentMessage', 'text': b.text})
                            elif isinstance(b, ServerToolUseBlock):
                                server_tools.append(b.name)
                    elif isinstance(m, ResultMessage):
                        haiku_results.append({'subtype': m.subtype, 'num_turns': m.num_turns, 'total_cost_usd': m.total_cost_usd,
                                              'model_usage': m.model_usage, 'terminal_reason': m.terminal_reason})

        lease = tunnel = None
        ssh_log = (private / 'ssh.log').open('w')
        sol_result = None
        try:
            try:
                await haiku_stage()
            except Exception as exc:  # a failed cheap attempt still goes to Sol; its error is recorded
                record['haiku_error'] = f'{type(exc).__name__}: {exc}'[:500]
            record['haiku'] = {'cost_usd': haiku_results[-1]['total_cost_usd'] if haiku_results else None,
                               'results': haiku_results, 'native_server_tool_calls': server_tools,
                               'models_in_session': sorted({k for r in haiku_results for k in (r['model_usage'] or {})})}

            # ---------- Stage 2: Sol verifies first in the same workspace ----------
            files_before = await self._snapshot(environment)
            transcript = render(completed_items(events_path))
            if len(transcript) > TRANSCRIPT_CHARS:
                transcript = f'[{len(transcript) - TRANSCRIPT_CHARS} earlier chars omitted]…\n' + transcript[-TRANSCRIPT_CHARS:]
            prompt = f'ORIGINAL TASK:\n{instruction}\n\nEARLIER MODEL TRANSCRIPT:\n{transcript or "(no actions)"}'
            sandbox = environment._sandbox
            if sandbox is None:
                raise RuntimeError('No Daytona sandbox')
            home = private / 'sol-home'
            home.mkdir(mode=0o700)
            shutil.copyfile(self._resolve_auth_json_path(), home / 'auth.json')
            (home / 'auth.json').chmod(0o600)
            (home / 'config.toml').write_text('model="gpt-6.1-sol"\nmodel_reasoning_effort="high"\nservice_tier="default"\n')
            check = await asyncio.create_subprocess_exec(CODEX, '--version', stdout=asyncio.subprocess.PIPE)
            if (await check.communicate())[0].decode().strip() != 'codex-cli 0.162.0-alpha.17.2':
                raise RuntimeError('Host Codex version drift')
            await self.exec_as_agent(environment, command=(
                'nohup /usr/local/bin/codex exec-server --listen ws://127.0.0.1:38761 '
                '>/logs/agent/exec-server.log 2>&1 </dev/null & echo $! >/tmp/sol-exec-server.pid'))
            lease = await sandbox.create_ssh_access(expires_in_minutes=540)
            host, port = ssh_destination(lease.ssh_command, lease.token)
            config = private / 'ssh-config'
            config.write_text(f'Host sol-executor\n HostName {host}\n Port {port}\n User {lease.token}\n'
                              f' StrictHostKeyChecking accept-new\n UserKnownHostsFile {private}/known_hosts\n'
                              ' BatchMode yes\n ExitOnForwardFailure yes\n ServerAliveInterval 15\n ServerAliveCountMax 3\n')
            config.chmod(0o600)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                local_port = sock.getsockname()[1]
            tunnel = await asyncio.create_subprocess_exec('ssh', '-F', str(config), '-N', '-L',
                f'127.0.0.1:{local_port}:127.0.0.1:38761', 'sol-executor', stdout=ssh_log, stderr=ssh_log)
            for _ in range(100):
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
            sol = SolSession(home, self.logs_dir / 'sol', 'gpt-6.1-sol', 'high')
            try:
                await sol.start()
                sol_result = await sol.turn(f'ws://127.0.0.1:{local_port}', prompt, VERIFY_FIRST, '/app',
                                            budget_usd=SOL_CAP[task])
            finally:
                await sol.close()
            changed, truncated = await self._changed(environment)
            record['sol'] = {'cost_usd': sol_result['usage']['cost_usd'], 'status': sol_result['status'],
                             'interrupted': sol_result.get('interrupted'), 'commands': sol_result['evidence'].get('command_count'),
                             'changed_files': changed[:200], 'changed_file_count': len(changed),
                             'files_hashed_before': files_before, 'changed_listing_truncated': truncated,
                             'final_text': (sol_result['evidence'].get('text') or '')[-1500:]}
            record['accepted_without_edits'] = len(changed) == 0
        finally:
            costs = [record.get('haiku', {}).get('cost_usd'), record.get('sol', {}).get('cost_usd')]
            record['total_cost_usd'] = sum(costs) if all(isinstance(c, (int, float)) for c in costs) else None
            record['usage_complete'] = record['total_cost_usd'] is not None
            record['native_claude_advisor_disabled'] = True
            record['verifier_feedback'] = False
            record['cost_basis'] = 'standard token list-price equivalent, not invoice; infrastructure excluded'
            (self.logs_dir / 'v65-usage.json').write_text(json.dumps(record, indent=2) + '\n')
            if tunnel is not None and tunnel.returncode is None:
                tunnel.terminate()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(tunnel.wait(), 10)
                if tunnel.returncode is None:
                    tunnel.kill()
                    await tunnel.wait()
            if lease is not None:
                with contextlib.suppress(Exception):
                    await environment._sandbox.revoke_ssh_access(lease.token)
            ssh_log.close()
            with contextlib.suppress(Exception):
                await environment.upload_dir(self.logs_dir, str(self.environment_logs_dir))
            shutil.rmtree(private)
