"""Harbor adapter for native Codex with a Daytona exec-server.

The host runs the official model/tool loop. The official Linux exec-server
runs every task tool inside the original Daytona task container. An SSH
local forward carries inbound executor RPC only; no sandbox egress proxy.
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
from native_remote import NativeRemote, CODEX
from transport_codex import StagedCodex


class RemoteNativeCodex(StagedCodex):
    async def install(self, environment):
        await super().install(environment)
        if environment.environment_dir.parent.name == 'cad-model':
            # Offline copies of public package artifacts compensate for the
            # provider's broken essential-service egress. No CAD code or hints.
            await environment.upload_file(Path('/private/tmp/luna-tb4-runtime/cad-wheelhouse.tar'), '/tmp/cad-wheelhouse.tar')
            await environment.upload_file(Path('/private/tmp/luna-tb4-runtime/cad-system-debs.tar'), '/tmp/cad-system-debs.tar')
            await self.exec_as_root(environment, command=(
                'mkdir -p /opt/luna-offline-wheels /tmp/luna-cad-debs && '
                'tar -xf /tmp/cad-wheelhouse.tar -C /opt/luna-offline-wheels && '
                'tar -xf /tmp/cad-system-debs.tar -C /tmp/luna-cad-debs && '
                'DEBIAN_FRONTEND=noninteractive dpkg -i /tmp/luna-cad-debs/*.deb '
                '>/logs/agent/cad-system-setup.log 2>&1 && '
                "printf '[global]\\nno-index = true\\nfind-links = /opt/luna-offline-wheels\\n' > /etc/pip.conf && "
                'rm /tmp/cad-wheelhouse.tar /tmp/cad-system-debs.tar && rm -r /tmp/luna-cad-debs'))

    async def run(self, instruction, environment, context):
        if self.model_name not in ('openai/gpt-6.1-sol', 'openai/gpt-6-astra') or self.options.reasoning_effort != 'high':
            raise RuntimeError('Unexpected benchmark model')
        model = self.model_name.removeprefix('openai/')
        effort = self.options.reasoning_effort
        sandbox = environment._sandbox
        if sandbox is None:
            raise RuntimeError('No Daytona sandbox')
        private = Path(tempfile.mkdtemp(prefix='advisor-screen-native-', dir='/private/tmp'))
        home = private / 'home'
        home.mkdir(mode=0o700)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._resolve_auth_json_path(), home / 'auth.json')
        (home / 'auth.json').chmod(0o600)
        (home / 'config.toml').write_text(f'model="{model}"\nmodel_reasoning_effort="{effort}"\nservice_tier="default"\n')
        lease = None
        tunnel = None
        ssh_log = (private / 'ssh.log').open('w')
        client = NativeRemote(home, self.logs_dir, model, effort)
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
            outcome = await client.run(f'ws://127.0.0.1:{local_port}', instruction, cwd=cwd)
            (self.logs_dir / 'native-outcome.json').write_text(json.dumps(outcome, indent=2) + '\n')
            (self.logs_dir / 'transport.json').write_text(json.dumps({
                'model': self.model_name, 'reasoning_effort': effort, 'advisor': False,
                'host_runtime': version, 'task_tools': 'native Codex exec-server in Daytona',
                'sandbox_id': sandbox.id, 'task_cwd': cwd,
                'instruction_sha256': hashlib.sha256(instruction.encode()).hexdigest(),
                'native_app_server': True, 'native_cli_exec': False,
                'sandbox_egress_proxy': False, 'verifier_feedback': False,
            }, indent=2) + '\n')
        finally:
            await client.close()
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
            # Only sanitized native logs are mirrored; credentials stay local
            # in the private temporary directory, which is then removed.
            with contextlib.suppress(Exception):
                await environment.upload_dir(self.logs_dir, str(self.environment_logs_dir))
            shutil.rmtree(private)
