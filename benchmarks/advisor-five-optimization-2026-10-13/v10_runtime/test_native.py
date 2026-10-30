import json,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from v10_runtime.native import AdvisorSession
from hybrid_native import Session

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_expert_matches_solo_without_changing_remote_scope_or_effort(self):
        with tempfile.TemporaryDirectory() as folder:
            client=AdvisorSession.__new__(AdvisorSession);client.log_dir=Path(folder)
            params={'model':'gpt-6.1-sol','config':{'web_search':'disabled','model_reasoning_effort':'high','features':{'multi_agent':False}},'environments':[{'environmentId':'daytona','cwd':'/app'}]}
            with patch.object(Session,'rpc',new=AsyncMock(return_value={'ok':True})) as call:
                await client.rpc('thread/start',params)
                forwarded=call.call_args.args[1]
                self.assertNotIn('web_search',forwarded['config'])
                self.assertEqual(forwarded['environments'],params['environments'])
                self.assertFalse(forwarded['config']['features']['multi_agent'])
                self.assertEqual(forwarded['config']['model_reasoning_effort'],'high')
                self.assertEqual(params['config']['web_search'],'disabled')
            self.assertEqual(json.loads((Path(folder)/'native-capabilities.json').read_text())['public_web'],'provider_default_matching_solo')

    async def test_other_rpc_calls_are_not_rewritten(self):
        client=AdvisorSession.__new__(AdvisorSession)
        params={'threadId':'example','input':[]}
        with patch.object(Session,'rpc',new=AsyncMock()) as call:
            await client.rpc('turn/start',params)
            self.assertIs(call.call_args.args[1],params)

if __name__=='__main__':unittest.main()
