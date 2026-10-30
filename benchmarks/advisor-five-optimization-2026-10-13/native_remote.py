"""Native Codex app-server client; all solver tools use one remote executor.

Model traffic stays on the host. This does not proxy sandbox internet traffic.
No benchmark solutions or verifier results enter this client.
"""
import asyncio
import json
import os
from pathlib import Path

CODEX = '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'


class NativeRemote:
    def __init__(self, home, log_dir, model, effort):
        self.model = model
        self.effort = effort
        self.home = Path(home)
        self.log_dir = Path(log_dir)
        self.pending = {}
        self.notifications = asyncio.Queue()
        self.counter = 0

    async def start(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'TERM')}
        env['CODEX_HOME'] = str(self.home)
        self.stderr = (self.log_dir / 'app-server.stderr.log').open('w')
        self.events = (self.log_dir / 'app-server.events.jsonl').open('w')
        self.proc = await asyncio.create_subprocess_exec(
            CODEX, 'app-server', '--stdio', cwd=str(self.home), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.stderr,
            limit=32 * 1024 * 1024)
        self.reader = asyncio.create_task(self.read())
        await self.rpc('initialize', {'clientInfo': {'name': 'harbor_codex_remote', 'title': 'Harbor Codex remote transport', 'version': '1.0'}, 'capabilities': {'experimentalApi': True}})
        await self.send({'method': 'initialized'})

    async def send(self, message):
        self.proc.stdin.write((json.dumps(message) + '\n').encode())
        await self.proc.stdin.drain()

    async def rpc(self, method, params):
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self.send({'id': request_id, 'method': method, 'params': params})
        return await asyncio.wait_for(future, 90)

    async def read(self):
        try:
            while line := await self.proc.stdout.readline():
                message = json.loads(line)
                self.events.write(json.dumps(message) + '\n')
                self.events.flush()
                if 'id' in message and 'method' not in message:
                    future = self.pending.pop(message['id'], None)
                    if future and not future.done():
                        if 'error' in message:
                            future.set_exception(RuntimeError(json.dumps(message['error'])))
                        else:
                            future.set_result(message.get('result'))
                elif 'id' in message:
                    # An unattended benchmark must not silently answer a new
                    # human approval or clarification request.
                    await self.send({'id': message['id'], 'error': {'code': -32603, 'message': 'No interactive approval handler in benchmark'}})
                    await self.notifications.put({'method': 'transport/unexpected_request', 'params': {'method': message['method']}})
                else:
                    await self.notifications.put(message)
        finally:
            for future in self.pending.values():
                if not future.done(): future.set_exception(RuntimeError('Codex app-server closed'))
            await self.notifications.put({'method': 'transport/closed'})

    async def run(self, url, instruction, sandbox='danger-full-access', cwd='/app'):
        await self.rpc('environment/add', {'environmentId': 'daytona', 'execServerUrl': url, 'connectTimeoutMs': 30000})
        thread = await self.rpc('thread/start', {
            'model': self.model, 'allowProviderModelFallback': False,
            'approvalPolicy': 'never', 'sandbox': sandbox,
            'environments': [{'environmentId': 'daytona', 'cwd': cwd, 'runtimeWorkspaceRoots': [cwd]}],
            'config': {'model_reasoning_effort': self.effort, 'service_tier': 'default', 'features': {'unified_exec': True}},
            'experimentalRawEvents': True,
        })
        if thread.get('model') != self.model or thread.get('reasoningEffort') != self.effort:
            raise RuntimeError('Requested model/effort not selected: ' + str({k: thread.get(k) for k in ('model', 'reasoningEffort')}))
        thread_id = thread['thread']['id']
        started = await self.rpc('turn/start', {'threadId': thread_id, 'effort': self.effort, 'input': [{'type': 'text', 'text': instruction, 'text_elements': []}]})
        turn_id = started['turn']['id']
        while True:
            event = await self.notifications.get()
            if event.get('method', '').startswith('transport/'):
                raise RuntimeError(str(event))
            if event.get('method') == 'turn/completed' and event['params'].get('threadId') == thread_id and event['params']['turn']['id'] == turn_id:
                turn = event['params']['turn']
                if turn.get('status') != 'completed': raise RuntimeError('Native turn failed: ' + json.dumps(turn))
                return {'thread_id': thread_id, 'turn': turn, 'thread': thread['thread']}

    async def close(self):
        if hasattr(self, 'proc'):
            if self.proc.returncode is None:
                self.proc.terminate()
                try: await asyncio.wait_for(self.proc.wait(), 10)
                except TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()
            await self.reader
            self.events.close()
            self.stderr.close()
