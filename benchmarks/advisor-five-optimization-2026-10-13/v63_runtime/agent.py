"""V63 official advisor pattern (V62 + Sol advisor on every task, serialized advisor calls).

LunaSolo:          Luna medium does the task alone (same transport as the Sol solo baselines).
LunaWithAdvisor:   Luna medium does all the work; it may call `advisor`, which sees its transcript and
                   returns short advice (Sol high for CAD/HOF/SATB, Grok high for WAL/ATRX). One
                   final-review nudge if no advice followed the last work.
No hidden tests, grader output or earlier solutions are given to either model.
"""
import asyncio
import contextlib
import hashlib
import json
import shutil
import socket
import tempfile
from pathlib import Path

from harbor.environments.daytona.ssh import ssh_destination
from hybrid_native import account_codex, read_events
from native_remote import NativeRemote, CODEX
from remote_native_codex import RemoteNativeCodex
from .advisor import Advisor, ADVISOR_TOOL, EXECUTOR_POLICY, FINAL_REVIEW_NUDGE, MAX_CALLS, load_images
from .native import ExecutorSession

ROOT = Path(__file__).resolve().parent.parent
ADVISORS = {'cad-model': ('gpt-6.1-sol', 'high'), 'hof-topology-interpenetration': ('gpt-6.1-sol', 'high'),
            'satb-audio-transcription': ('gpt-6.1-sol', 'high'),
            # V63: Sol for WAL/ATRX too; the no-tools Grok/Cursor answer dropped at ~3 min of reasoning in V62.
            'wal-recovery-ordering': ('gpt-6.1-sol', 'high'), 'atrx-vep-crispr': ('gpt-6.1-sol', 'high')}


class LunaSolo(RemoteNativeCodex):
    WITH_ADVISOR = False

    async def run(self, instruction, environment, context):
        if self.model_name != 'openai/gpt-6-luna' or self.options.reasoning_effort != 'medium':
            raise RuntimeError('Unexpected benchmark model')
        model, effort = 'gpt-6-luna', 'medium'
        task = environment.environment_dir.parent.name
        sandbox = environment._sandbox
        if sandbox is None:
            raise RuntimeError('No Daytona sandbox')
        private = Path(tempfile.mkdtemp(prefix='v62-native-', dir='/private/tmp'))
        home = private / 'home'
        home.mkdir(mode=0o700)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._resolve_auth_json_path(), home / 'auth.json')
        (home / 'auth.json').chmod(0o600)
        (home / 'config.toml').write_text(f'model="{model}"\nmodel_reasoning_effort="{effort}"\nservice_tier="default"\n')
        advisor = None
        if self.WITH_ADVISOR:
            adv_model, adv_effort = ADVISORS[task]
            images, image_shas = load_images(environment.environment_dir) if adv_model == 'gpt-6.1-sol' else ([], [])
            advisor = Advisor(private, self.logs_dir, self.logs_dir / 'app-server.events.jsonl', instruction,
                              adv_model, adv_effort, images)
            advisor.setup(self._resolve_auth_json_path())
            client = ExecutorSession(home, self.logs_dir, model, effort, tool_handler=advisor.handle)
        else:
            client = NativeRemote(home, self.logs_dir, model, effort)
        lease = tunnel = None
        ssh_log = (private / 'ssh.log').open('w')
        nudged = False
        try:
            check = await asyncio.create_subprocess_exec(CODEX, '--version', stdout=asyncio.subprocess.PIPE)
            version = (await check.communicate())[0].decode().strip()
            if version != 'codex-cli 0.162.0-alpha.17.2':
                raise RuntimeError('Host Codex version drift')
            cwd_result = await environment.exec(command='pwd')
            if cwd_result.return_code:
                raise RuntimeError('Cannot determine task cwd')
            cwd = cwd_result.stdout.strip()
            await self.exec_as_agent(environment, command=(
                'nohup /usr/local/bin/codex exec-server --listen ws://127.0.0.1:38761 '
                '>/logs/agent/exec-server.log 2>&1 </dev/null & echo $! >/tmp/luna-exec-server.pid'))
            lease = await sandbox.create_ssh_access(expires_in_minutes=540)
            host, port = ssh_destination(lease.ssh_command, lease.token)
            config = private / 'ssh-config'
            config.write_text(f'Host luna-executor\n HostName {host}\n Port {port}\n User {lease.token}\n'
                              f' StrictHostKeyChecking accept-new\n UserKnownHostsFile {private}/known_hosts\n'
                              ' BatchMode yes\n ExitOnForwardFailure yes\n ServerAliveInterval 15\n ServerAliveCountMax 3\n')
            config.chmod(0o600)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                local_port = sock.getsockname()[1]
            tunnel = await asyncio.create_subprocess_exec('ssh', '-F', str(config), '-N', '-L',
                f'127.0.0.1:{local_port}:127.0.0.1:38761', 'luna-executor', stdout=ssh_log, stderr=ssh_log)
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
            await client.start()
            url = f'ws://127.0.0.1:{local_port}'
            if advisor is None:
                outcome = await client.run(url, instruction, cwd=cwd)
            else:
                outcome = await client.run_with(url, instruction, cwd, {'developerInstructions': EXECUTOR_POLICY,
                                                                         'dynamicTools': [ADVISOR_TOOL]})
                if advisor.needs_final_review():
                    nudged = True
                    outcome['follow_up_turn'] = await client.follow_up(outcome['thread_id'], FINAL_REVIEW_NUDGE)
            (self.logs_dir / 'native-outcome.json').write_text(json.dumps(outcome, indent=2) + '\n')
        finally:
            await client.close()
            if advisor is not None:
                advisor.abandon_in_flight()
            self._write_usage(advisor, nudged)
            if (home / 'sessions').exists():
                shutil.copytree(home / 'sessions', self.logs_dir / 'sessions', dirs_exist_ok=True)
            if tunnel is not None and tunnel.returncode is None:
                tunnel.terminate()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(tunnel.wait(), 10)
                if tunnel.returncode is None:
                    tunnel.kill()
                    await tunnel.wait()
            if lease is not None:
                with contextlib.suppress(Exception):
                    await sandbox.revoke_ssh_access(lease.token)
            ssh_log.close()
            with contextlib.suppress(Exception):
                await environment.upload_dir(self.logs_dir, str(self.environment_logs_dir))
            shutil.rmtree(private)

    def _write_usage(self, advisor, nudged):
        events = self.logs_dir / 'app-server.events.jsonl'
        executor = account_codex(read_events(events), 'gpt-6-luna')['cost_usd'] if events.exists() else None
        calls = advisor.calls if advisor else []
        advisor_cost = sum(c['cost_usd'] for c in calls) if all(c.get('cost_usd') is not None for c in calls) else None
        complete = executor is not None and (advisor is None or advisor_cost is not None)
        (self.logs_dir / 'v62-usage.json').write_text(json.dumps({
            'pattern': 'official_advisor' if advisor else 'executor_solo',
            'executor': {'model': 'gpt-6-luna', 'effort': 'medium', 'cost_usd': executor},
            'advisor': ({'model': advisor.model, 'effort': advisor.effort, 'max_calls': MAX_CALLS,
                         'calls': calls, 'cost_usd': advisor_cost, 'final_review_nudge_sent': nudged}
                        if advisor else None),
            'total_cost_usd': (executor + (advisor_cost or 0)) if complete else None,
            'usage_complete': complete,
            'verifier_feedback': False,
            'cost_basis': 'standard token list-price equivalent, not invoice; infrastructure excluded',
        }, indent=2) + '\n')


class LunaWithAdvisor(LunaSolo):
    WITH_ADVISOR = True
