"""Diagnostic agent: why does a no-tool SDK session carry ~30k tokens of context inside Harbor but ~640 outside?
Records environment variable NAMES (never values), cwd, and a minimal Haiku session's cache-creation tokens."""
import json
import os
import tempfile
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
from harbor.agents.base import BaseAgent


class EnvProbe(BaseAgent):
    @staticmethod
    def name():
        return 'env-probe'

    def version(self):
        return '1'

    async def setup(self, environment):
        return None

    async def _measure(self, env_overrides):
        opts = ClaudeAgentOptions(model='claude-haiku-5-5', tools=[], setting_sources=[], cwd=tempfile.mkdtemp(),
                                  permission_mode='bypassPermissions', system_prompt='You are terse.', max_turns=1,
                                  env={'CLAUDE_CODE_DISABLE_ADVISOR_TOOL': '1', **env_overrides})
        async with ClaudeSDKClient(opts) as c:
            await c.query('Reply with the single word OK.')
            async for m in c.receive_response():
                if isinstance(m, ResultMessage):
                    u = m.model_usage['claude-haiku-5-5']
                    return {'cache_creation': u.get('cacheCreationInputTokens'), 'cache_read': u.get('cacheReadInputTokens')}

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        out = {'env_names': sorted(os.environ), 'cwd': os.getcwd(), 'home': os.environ.get('HOME')}
        out['default'] = await self._measure({})
        (self.logs_dir / 'envprobe.json').write_text(json.dumps(out, indent=2) + '\n')


class FileListProbe(EnvProbe):
    """No model call: list what the lean arms' FILE_LIST collects inside the container."""
    async def run(self, instruction, environment, context):
        import shlex
        from arms import FILE_LIST
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        r = await environment.exec(command='bash -lc ' + shlex.quote(FILE_LIST + ' | xargs -I{} wc -c {}'), cwd='/app', timeout_sec=60)
        (self.logs_dir / 'filelist.txt').write_text((r.stdout or '') + '\nINSTRUCTION_CHARS ' + str(len(instruction)) + '\n')
