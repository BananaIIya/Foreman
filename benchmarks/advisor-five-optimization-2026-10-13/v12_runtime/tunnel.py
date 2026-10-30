"""Recover a dead SSH forwarding process without replaying task commands."""
import asyncio
import contextlib
import json


class TunnelSupervisor:
    def __init__(self, spawn, ready, emit, max_restarts=3):
        self.spawn, self.ready, self.emit = spawn, ready, emit
        self.max_restarts = max_restarts
        self.process = None
        self.task = None
        self.available = asyncio.Event()
        self.failed = asyncio.Event()
        self.error = None
        self.restarts = 0

    async def start(self):
        self.task = asyncio.create_task(self._watch())
        await self.ensure_ready()

    async def ensure_ready(self):
        await self.available.wait()
        if self.error:
            raise RuntimeError('SSH executor unavailable; no new model call permitted') from self.error
        # The process may have exited just before the watcher cleared its event.
        if self.process is None or self.process.returncode is not None:
            self.available.clear()
            return await self.ensure_ready()

    async def _watch(self):
        try:
            while True:
                self.process = await self.spawn()
                await self.ready(self.process)
                self.emit({'event': 'tunnel_ready', 'restart': self.restarts})
                self.available.set()
                code = await self.process.wait()
                self.available.clear()
                self.emit({'event': 'tunnel_exited', 'exit_code': code,
                           'restart': self.restarts, 'task_commands_replayed': 0})
                if self.restarts >= self.max_restarts:
                    raise RuntimeError('SSH tunnel restart limit reached')
                self.restarts += 1
                await asyncio.sleep(min(.2 * self.restarts, 1))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = exc
            self.failed.set()
            self.available.set()
            self.emit({'event': 'tunnel_unavailable', 'error_type': type(exc).__name__,
                       'restart': self.restarts})

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()


async def guarded_turn(client, tunnel, call):
    """Interrupt a live native turn if forwarding recovery has definitively failed.

    Keep the native result/usage; never reissue the original turn or shell command.
    """
    await tunnel.ensure_ready()
    turn = asyncio.create_task(call())
    failure = asyncio.create_task(tunnel.failed.wait())
    try:
        done, _ = await asyncio.wait((turn, failure), return_when=asyncio.FIRST_COMPLETED)
        if turn in done:
            return await turn
        # The log is our own native protocol evidence, not a model instruction.
        from hybrid_native import read_events
        events = read_events(client.log_dir / 'app-server.events.jsonl')
        starts = [e['params'] for e in events if e.get('method') == 'turn/started']
        if starts:
            last = starts[-1]
            with contextlib.suppress(RuntimeError):
                await client.rpc('turn/interrupt', {
                    'threadId': last['threadId'], 'turnId': last['turn']['id']})
            result = await turn
            result['transport_failure'] = 'ssh_recovery_exhausted'
            (client.log_dir / 'phase-result.json').write_text(json.dumps(result, indent=2) + '\n')
            return result
        # No native turn has started, so do not permit a later paid start.
        turn.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await turn
        raise RuntimeError('SSH recovery failed before native turn start')
    finally:
        failure.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await failure
        if not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn
