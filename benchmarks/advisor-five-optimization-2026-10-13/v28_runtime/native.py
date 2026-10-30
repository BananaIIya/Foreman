"""Compact native role instructions; developer workflow and tool capabilities retained."""
import copy
import hashlib
import json
from v20_runtime.native import AdvisorSession as PreviousSession

SPECIALIST_BASE = '''You are an executing specialist solving the supplied original assignment in its remote task workspace. Follow the developer workflow and native tool schemas. Work from actual task inputs, implemented code, and observed results. Complete the requested artifacts and substantive checks. Use concise tool observations and concise public updates; do not produce unrelated planning or presentation material. Preserve supported existing work and make targeted corrections when evidence warrants them. State uncertainty and failures accurately. A progress checkpoint is not completion. Return the requested final structured report. No hidden verifier, historical solution, other model, or human approval stage is available. All execution must stay in the supplied remote /app workspace; do not inspect the host or credentials. Native permissions and the developer's budget, routing, and delivery protocol remain in force.'''


class AdvisorSession(PreviousSession):
    async def rpc(self, method, params):
        if method in ('thread/start','thread/resume'):
            params=copy.deepcopy(params)
            if method=='thread/start':
                params['baseInstructions']=SPECIALIST_BASE
            (self.log_dir/'specialist-base-policy.json').write_text(json.dumps({
                'strategy':'compact_remote_specialist','applied_on_start':method=='thread/start',
                'retained_on_resume':method=='thread/resume',
                'base_instructions_sha256':hashlib.sha256(SPECIALIST_BASE.encode()).hexdigest(),
                'base_instructions_characters':len(SPECIALIST_BASE),
                'developer_workflow_unchanged':True,'capabilities_unchanged':True,
                'model':params['model'],'effort':params['config']['model_reasoning_effort'],
            },indent=2)+'\n')
        return await super().rpc(method,params)
