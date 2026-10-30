"""Bound retained tool history while preserving remote files and native usage."""
import copy
import json
from v10_runtime.native import AdvisorSession as NativeAdvisorSession

TOOL_OUTPUT_TOKEN_LIMIT = 2048


class AdvisorSession(NativeAdvisorSession):
    async def rpc(self, method, params):
        if method in ('thread/start', 'thread/resume'):
            params = copy.deepcopy(params)
            params['config']['tool_output_token_limit'] = TOOL_OUTPUT_TOKEN_LIMIT
            (self.log_dir / 'observation-policy.json').write_text(json.dumps({
                'tool_output_token_limit': TOOL_OUTPUT_TOKEN_LIMIT,
                'source': 'https://developers.openai.com/codex/config-reference/',
                'scope': 'retained individual tool output; not model output tokens',
                'data_remains_in_remote_workspace': True,
            }, indent=2) + '\n')
        return await super().rpc(method, params)
