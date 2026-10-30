"""Use the provider's original native role, as in the verified solo harness."""
import json
from v20_runtime.native import AdvisorSession as NativeDefaultSession
from v39_runtime.native import AdvisorSession as RepairWarningSession


class AdvisorSession(NativeDefaultSession):
    budget_notice = RepairWarningSession.budget_notice

    async def rpc(self, method, params):
        if method in ('thread/start', 'thread/resume'):
            if 'baseInstructions' in params:
                raise ValueError('Native-default specialist must not override provider base instructions')
            (self.log_dir/'specialist-base-policy.json').write_text(json.dumps({
                'strategy': 'provider_default_matching_verified_solo',
                'custom_base_instructions_supplied': False,
                'applied_on_start': method == 'thread/start',
                'retained_on_resume': method == 'thread/resume',
                'developer_workflow_unchanged': True,
                'model': params['model'],
                'effort': params['config']['model_reasoning_effort'],
            }, indent=2)+'\n')
        return await super().rpc(method, params)
