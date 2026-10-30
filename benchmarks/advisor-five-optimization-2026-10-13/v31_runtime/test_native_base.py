import copy,tempfile,unittest
from unittest.mock import AsyncMock,patch
from v20_runtime.native import AdvisorSession as PreviousSession
from .native import AdvisorSession,SPECIALIST_BASE


class SpecialistBaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_start_base_changes_and_resume_keeps_context(self):
        with tempfile.TemporaryDirectory() as folder:
            c=AdvisorSession(folder,folder,'gpt-6.1-sol','high')
            original={'model':'gpt-6.1-sol','approvalPolicy':'never','sandbox':'danger-full-access',
                      'environments':[{'environmentId':'daytona','cwd':'/app'}],
                      'developerInstructions':'Original workflow, budgets and delivery requirements',
                      'config':{'model_reasoning_effort':'high','features':{'unified_exec':True,'multi_agent':False}}}
            saved=copy.deepcopy(original)
            with patch.object(PreviousSession,'rpc',new_callable=AsyncMock) as rpc:
                await c.rpc('thread/start',original)
                sent=rpc.call_args.args[1]
                self.assertEqual(sent.pop('baseInstructions'),SPECIALIST_BASE)
                self.assertEqual(sent,original)
                self.assertEqual(original,saved)
                resume=dict(original,threadId='same-native-task')
                await c.rpc('thread/resume',resume)
                self.assertEqual(rpc.call_args.args[1],resume)
                self.assertNotIn('baseInstructions',rpc.call_args.args[1])


if __name__=='__main__':unittest.main()
