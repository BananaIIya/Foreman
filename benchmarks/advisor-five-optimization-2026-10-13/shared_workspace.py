"""One native remote executor shared serially by worker and specialist."""
import asyncio
import contextlib
import shutil
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from harbor.environments.daytona.ssh import ssh_destination


def make_home(private, role, model, effort):
    home = Path(private) / role
    home.mkdir(mode=0o700, exist_ok=True)
    auth = home / 'auth.json'
    if not auth.exists():
        shutil.copyfile(Path.home() / '.codex/auth.json', auth)
        auth.chmod(0o600)
    (home / 'config.toml').write_text(f'model="{model}"\nmodel_reasoning_effort="{effort}"\nservice_tier="default"\n')
    return home


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
        sock.bind(('127.0.0.1', 0)); local_port = sock.getsockname()[1]
    log = (Path(private) / 'ssh.log').open('w')
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec('ssh', '-F', str(config), '-N', '-L',
            f'127.0.0.1:{local_port}:127.0.0.1:38761', 'hybrid-executor', stdout=log, stderr=log)
        for _ in range(100):
            if proc.returncode is not None:
                raise RuntimeError('SSH executor exited')
            try:
                _, writer = await asyncio.open_connection('127.0.0.1', local_port)
                writer.close(); await writer.wait_closed(); break
            except OSError:
                await asyncio.sleep(.2)
        else:
            raise RuntimeError('SSH executor not ready')
        yield f'ws://127.0.0.1:{local_port}'
    finally:
        if proc is not None and proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 10)
            if proc.returncode is None:
                proc.kill(); await proc.wait()
        with contextlib.suppress(Exception):
            await sandbox.revoke_ssh_access(lease.token)
        log.close()
