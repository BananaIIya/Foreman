"""Executor native Codex session with an advisor dynamic tool and an optional follow-up turn.

Same app-server/exec-server transport as the solo baselines (root native_remote.py); adds only
dynamic-tool dispatch and a second turn on the same thread for the final-review nudge.
"""
import asyncio
import json
from native_remote import NativeRemote


class ExecutorSession(NativeRemote):
    def __init__(self, home, log_dir, model, effort, tool_handler=None):
        super().__init__(home, log_dir, model, effort)
        self.tool_handler = tool_handler

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
                elif 'id' in message and message.get('method') == 'item/tool/call' and self.tool_handler is not None:
                    # Run the (slow) advisor call off the reader loop so events keep flowing.
                    asyncio.create_task(self._answer_tool(message))
                elif 'id' in message:
                    # An unattended benchmark must not silently answer approval or clarification requests.
                    await self.send({'id': message['id'], 'error': {'code': -32603, 'message': 'No interactive approval handler in benchmark'}})
                    await self.notifications.put({'method': 'transport/unexpected_request', 'params': {'method': message['method']}})
                else:
                    await self.notifications.put(message)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('Codex app-server closed'))
            await self.notifications.put({'method': 'transport/closed'})

    async def _answer_tool(self, message):
        try:
            result = await self.tool_handler(message['params'])
        except Exception as exc:  # the executor sees a failed tool call, never a fabricated answer
            result = {'success': False, 'contentItems': [{'type': 'inputText', 'text': f'Advisor unavailable ({type(exc).__name__}). Continue on your own judgment.'}]}
        await self.send({'id': message['id'], 'result': result})

    async def wait_turn(self, thread_id, turn_id):
        while True:
            event = await self.notifications.get()
            if event.get('method', '').startswith('transport/'):
                raise RuntimeError(str(event))
            if event.get('method') == 'turn/completed' and event['params'].get('threadId') == thread_id and event['params']['turn']['id'] == turn_id:
                turn = event['params']['turn']
                if turn.get('status') != 'completed':
                    raise RuntimeError('Native turn failed: ' + json.dumps(turn))
                return turn

    async def follow_up(self, thread_id, text):
        started = await self.rpc('turn/start', {'threadId': thread_id, 'effort': self.effort,
                                                'input': [{'type': 'text', 'text': text, 'text_elements': []}]})
        return await self.wait_turn(thread_id, started['turn']['id'])

    async def run_with(self, url, instruction, cwd, overrides):
        """Root NativeRemote.run plus thread overrides (developer instructions, dynamic tools)."""
        await self.rpc('environment/add', {'environmentId': 'daytona', 'execServerUrl': url, 'connectTimeoutMs': 30000})
        params = {
            'model': self.model, 'allowProviderModelFallback': False,
            'approvalPolicy': 'never', 'sandbox': 'danger-full-access',
            'environments': [{'environmentId': 'daytona', 'cwd': cwd, 'runtimeWorkspaceRoots': [cwd]}],
            'config': {'model_reasoning_effort': self.effort, 'service_tier': 'default', 'features': {'unified_exec': True}},
            'experimentalRawEvents': True,
        }
        params.update(overrides)
        thread = await self.rpc('thread/start', params)
        if thread.get('model') != self.model or thread.get('reasoningEffort') != self.effort:
            raise RuntimeError('Requested model/effort not selected: ' + str({k: thread.get(k) for k in ('model', 'reasoningEffort')}))
        thread_id = thread['thread']['id']
        started = await self.rpc('turn/start', {'threadId': thread_id, 'effort': self.effort,
                                                'input': [{'type': 'text', 'text': instruction, 'text_elements': []}]})
        turn = await self.wait_turn(thread_id, started['turn']['id'])
        return {'thread_id': thread_id, 'turn': turn, 'thread': thread['thread']}
