"""Match the solo's default public-web capability for remote expert sessions."""
import copy,json
from hybrid_native import Session

class AdvisorSession(Session):
    async def rpc(self,method,params):
        if method in ('thread/start','thread/resume'):
            params=copy.deepcopy(params)
            params['config'].pop('web_search',None)
            # Preserve all remote execution, model, effort and no-delegation
            # settings. Omitting this override matches the native solo harness.
            (self.log_dir/'native-capabilities.json').write_text(json.dumps({
                'public_web':'provider_default_matching_solo',
                'multi_agent':params['config']['features']['multi_agent'],
                'remote_environments':[e['environmentId'] for e in params.get('environments',[])],
                'model':params['model'],'effort':params['config']['model_reasoning_effort']},indent=2)+'\n')
        return await super().rpc(method,params)
