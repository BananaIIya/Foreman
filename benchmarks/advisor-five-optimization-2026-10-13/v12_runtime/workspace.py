"""Shared executor with bounded SSH recovery and retained redacted evidence."""
import asyncio
import contextlib
import json
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from harbor.environments.daytona.ssh import ssh_destination
from .tunnel import TunnelSupervisor


@asynccontextmanager
async def executor(agent, environment, private):
    sandbox = environment._sandbox
    if sandbox is None:
        raise RuntimeError('Daytona environment missing')
    await agent.exec_as_agent(environment, command=(
        'nohup /usr/local/bin/codex exec-server --listen ws://127.0.0.1:38761 '
        '>/logs/agent/exec-server.log 2>&1 </dev/null & echo $! >/tmp/hybrid-exec-server.pid'))
    lease = await sandbox.create_ssh_access(expires_in_minutes=540)
    host, port = ssh_destination(lease.ssh_command, lease.token)
    config = Path(private) / 'ssh-config'
    config.write_text(f'Host hybrid-executor\n HostName {host}\n Port {port}\n User {lease.token}\n'
                      f' StrictHostKeyChecking accept-new\n UserKnownHostsFile {private}/known_hosts\n'
                      ' BatchMode yes\n ExitOnForwardFailure yes\n ServerAliveInterval 15\n ServerAliveCountMax 3\n')
    config.chmod(0o600)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        local_port = sock.getsockname()[1]
    log_path = Path(private) / 'ssh.log'
    log = log_path.open('w')
    def emit(event):
        with (agent.logs_dir / 'executor-lifecycle.jsonl').open('a') as f:
            f.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **event}) + '\n')
    async def spawn():
        return await asyncio.create_subprocess_exec('ssh', '-F', str(config), '-N', '-L',
            f'127.0.0.1:{local_port}:127.0.0.1:38761', 'hybrid-executor', stdout=log, stderr=log)
    async def ready(proc):
        for _ in range(100):
            if proc.returncode is not None:
                raise RuntimeError('SSH executor exited during connection setup')
            try:
                _, writer = await asyncio.open_connection('127.0.0.1', local_port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(.2)
        raise RuntimeError('SSH executor local forwarding port not ready')
    supervisor = TunnelSupervisor(spawn, ready, emit)
    supervisor.url = f'ws://127.0.0.1:{local_port}'
    try:
        await supervisor.start()
        yield supervisor
    finally:
        await supervisor.close()
        with contextlib.suppress(Exception):
            await sandbox.revoke_ssh_access(lease.token)
        log.close()
        # Raw stderr stays private. The credential may appear as an SSH username.
        sanitized = log_path.read_text(errors='replace').replace(lease.token, '[REDACTED]')
        (agent.logs_dir / 'executor-ssh.redacted.log').write_text(sanitized)
