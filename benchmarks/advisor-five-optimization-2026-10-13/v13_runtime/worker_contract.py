"""Bounded native-worker configuration and data-only remote execution relay."""
from routing import PRICES
from v9_runtime.relay import CommandRelay


def worker_request(prompt, effort, cap, images):
    prices = dict(zip(['input', 'cache', 'write', 'output'],
                      [float(x) if x is not None else None for x in PRICES['grok-4.7']]))
    return {'model': 'grok-4.7', 'provider': 'cursor-sdk-remote-tools',
            'role': 'worker', 'effort': effort, 'prompt': prompt, 'images': images,
            'tools': ['mcp'], 'custom_tools': ['remote_command'],
            'setting_sources': [], 'mcp_servers': {}, 'budget_usd': cap,
            'command_limit': 2, 'native_turn_limit': 1, 'command_character_limit': 6000,
            'token_prices_per_million': prices, 'sdk_version': '1.0.37'}


class WorkerRelay(CommandRelay):
    def __init__(self, execute):
        super().__init__(execute, limit=2)

    async def handle(self, message):
        if len(self.commands) >= self.limit:
            return {'controller_error': 'Worker command limit reached; nothing executed'}
        command = message.get('command')
        if not isinstance(command, str) or not command.strip() or len(command) > 6000:
            row = {'command': command, 'started': False, 'rejected': True,
                   'controller_error': 'Worker commands must be nonempty and at most 6000 characters; nothing executed'}
            self.commands.append(row)
            return row
        return await super().handle(message)
