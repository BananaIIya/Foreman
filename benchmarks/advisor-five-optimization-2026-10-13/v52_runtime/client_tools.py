"""Native app-server client tools without blocking the notification reader.

Only explicit tool handlers are served. Human approvals remain unsupported.
Caller binds thread/turn membership and budget in the registered handler.
"""
import asyncio
import json


class ClientToolsMixin:
    def configure_client_tools(self, specs, handler):
        self.client_tool_specs = specs
        self.client_tool_handler = handler
        self.client_tool_tasks = set()

    async def rpc(self, method, params):
        if method == 'thread/start' and getattr(self, 'client_tool_specs', None):
            params = dict(params, dynamicTools=self.client_tool_specs)
        return await super().rpc(method, params)

    async def _serve_client_tool(self, message):
        try:
            value = await self.client_tool_handler(message['params'])
            result = {'contentItems': [{'type': 'inputText', 'text': json.dumps(value)}],
                      'success': value.get('status') not in (
                          'invalid_work_order', 'call_id_conflict', 'worker_unavailable',
                          'unknown_remaining_budget', 'insufficient_remaining_budget',
                          'wrong_native_session', 'unknown_tool')}
        except Exception as exc:
            result = {'contentItems': [{'type': 'inputText', 'text': json.dumps({
                'status': 'handler_failed', 'error_type': type(exc).__name__})}],
                'success': False}
        try:
            await self.send({'id': message['id'], 'result': result})
        except (BrokenPipeError, ConnectionError):
            # Actual worker result/fee must already be journaled by the handler.
            pass

    async def settle_client_tools(self):
        if getattr(self, 'client_tool_tasks', None):
            await asyncio.gather(*tuple(self.client_tool_tasks))

    async def read(self):
        try:
            while line := await self.proc.stdout.readline():
                message = json.loads(line)
                self.events.write(json.dumps(message)+'\n')
                self.events.flush()
                if 'id' in message and 'method' not in message:
                    future = self.pending.pop(message['id'], None)
                    if future and not future.done():
                        if 'error' in message:
                            future.set_exception(RuntimeError(json.dumps(message['error'])))
                        else:
                            future.set_result(message.get('result'))
                elif 'id' in message:
                    if (message.get('method') == 'item/tool/call'
                            and getattr(self, 'client_tool_handler', None)):
                        task = asyncio.create_task(self._serve_client_tool(message))
                        self.client_tool_tasks.add(task)
                        task.add_done_callback(self.client_tool_tasks.discard)
                    else:
                        await self.send({'id': message['id'], 'error': {
                            'code': -32603, 'message': 'No interactive approval handler in benchmark'}})
                        await self.notifications.put({'method': 'transport/unexpected_request',
                                                      'params': {'method': message['method']}})
                else:
                    await self.notifications.put(message)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('Codex app-server closed'))
            await self.notifications.put({'method': 'transport/closed'})

    async def close(self):
        # Do not abandon a paid worker when the expert happens to finish first.
        await self.settle_client_tools()
        await super().close()
