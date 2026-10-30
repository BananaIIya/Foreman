import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from .native import AdvisorSession, NativeSession, SPECIALIST_BASE


class BasePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_compact_role_preserves_developer_workflow_and_remote_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=AdvisorSession(Path(tmp),Path(tmp),'gpt-6.1-sol','high')
            params={'model':'gpt-6.1-sol','developerInstructions':'full original workflow',
                    'approvalPolicy':'never','sandbox':'danger-full-access',
                    'environments':[{'environmentId':'daytona','cwd':'/app'}],
                    'config':{'model_reasoning_effort':'high','features':{'multi_agent':False}}}
            before=copy.deepcopy(params)
            with patch.object(NativeSession,'rpc',return_value={'ok':True}) as rpc:
                await client.rpc('thread/start',params)
                actual=rpc.call_args.args[1]
                self.assertEqual(actual['baseInstructions'],SPECIALIST_BASE)
                self.assertEqual({k:v for k,v in actual.items() if k!='baseInstructions'},before)
                self.assertEqual(params,before)
                receipt=json.loads((Path(tmp)/'specialist-base-policy.json').read_text())
                self.assertEqual(receipt['base_instructions_sha256'],hashlib.sha256(SPECIALIST_BASE.encode()).hexdigest())
                self.assertTrue(receipt['capabilities_unchanged'])
                await client.rpc('thread/resume',params)
                self.assertNotIn('baseInstructions',rpc.call_args.args[1])
                self.assertTrue(json.loads((Path(tmp)/'specialist-base-policy.json').read_text())['retained_on_resume'])

    async def test_live_steer_is_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=AdvisorSession(Path(tmp),Path(tmp),'gpt-6.1-sol','high')
            params={'threadId':'same','expectedTurnId':'same-turn','input':[{'type':'text','text':'grounded concern'}]}
            with patch.object(NativeSession,'rpc',return_value={'ok':True}) as rpc:
                await client.rpc('turn/steer',params)
                self.assertEqual(rpc.call_args.args[1],params)


if __name__=='__main__':unittest.main()
