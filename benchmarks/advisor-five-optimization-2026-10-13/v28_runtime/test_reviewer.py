import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from hybrid_native import Session
from .reviewer import ReviewerSession, reviewed_decision


class ReviewerTests(unittest.IsolatedAsyncioTestCase):
    async def test_feature_limits_preserve_model_effort_and_original_params(self):
        with tempfile.TemporaryDirectory() as folder:
            client=ReviewerSession(folder,folder,'gpt-6-luna','medium')
            params={'model':'gpt-6-luna','config':{'model_reasoning_effort':'medium','features':{'unified_exec':True}}}
            with patch.object(Session,'rpc',new_callable=AsyncMock) as rpc:
                await client.rpc('thread/start',params)
                sent=rpc.call_args.args[1]
                self.assertEqual(sent['model'],'gpt-6-luna')
                self.assertEqual(sent['config']['model_reasoning_effort'],'medium')
                self.assertFalse(sent['config']['features']['shell_tool'])
                self.assertFalse(sent['config']['features']['apps'])
                self.assertFalse(sent['config']['features']['plugins'])
                self.assertIn('evidence-only',sent['baseInstructions'])
                self.assertTrue(params['config']['features']['unified_exec'])

    async def test_tool_violation_is_not_success_and_retains_native_cost(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            native={'status':'interrupted','interrupted':'decision_model_attempted_tool',
                    'usage':{'cost_usd':.0022},'evidence':{'text':'No review completed'}}
            (root/'phase-result.json').write_text(json.dumps(native))
            invoke=AsyncMock(side_effect=RuntimeError('decision_model_attempted_tool'))
            r=await reviewed_decision(invoke,root)
            self.assertEqual(invoke.await_count,1)
            self.assertEqual(r['decision']['action'],'advisor')
            self.assertEqual(r['status'],'interrupted')
            self.assertEqual(r['usage']['cost_usd'],.0022)
            self.assertFalse(r['controller_recovery']['success_claimed'])
            self.assertEqual(json.loads((root/'phase-result.json').read_text()),native)

    async def test_unrelated_transport_errors_are_not_masked(self):
        with self.assertRaisesRegex(RuntimeError,'transport closed'):
            await reviewed_decision(AsyncMock(side_effect=RuntimeError('transport closed')),Path('/nonexistent'))

    async def test_valid_result_is_untouched_and_not_retried(self):
        native={'status':'completed','evidence':{'text':'{"action":"deliver"}'},'usage':{'cost_usd':.001}}
        invoke=AsyncMock(return_value=native)
        self.assertIs(await reviewed_decision(invoke,Path('/nonexistent')),native)
        self.assertEqual(invoke.await_count,1)


if __name__=='__main__':unittest.main()
