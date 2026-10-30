import unittest
from v8_runtime.context import continuation_payload
from hybrid_native import account_codex

class Tests(unittest.TestCase):
    def test_only_verified_unchanged_expert_source_is_omitted(self):
        payload={'original_task':'retained in native thread','response_schema':{},'worker_evidence':{'text':'new observations'},
            'workspace_excerpts':[{'path':'/app/a.py','text':'old code','sha256':'same'},
                {'path':'/app/b.py','text':'changed code','sha256':'new'},
                {'path':'/app/c.txt','text':'unverified','truncated':True}]}
        result=continuation_payload(payload,{'/app/a.py':'same','/app/b.py':'old'})
        self.assertNotIn('original_task',result);self.assertNotIn('response_schema',result)
        self.assertNotIn('text',result['workspace_excerpts'][0])
        self.assertEqual(result['workspace_excerpts'][1]['text'],'changed code')
        self.assertEqual(result['workspace_excerpts'][2]['text'],'unverified')
        self.assertEqual(result['worker_evidence']['text'],'new observations')
        self.assertEqual(payload['workspace_excerpts'][0]['text'],'old code')

    def test_resumed_turn_usage_does_not_rebill_prior_turn(self):
        def event(turn,total,last):return {'method':'thread/tokenUsage/updated','params':{'threadId':'same-thread','turnId':turn,'tokenUsage':{'total':{'totalTokens':total},'last':last}}}
        old={'inputTokens':1000,'cachedInputTokens':0,'cacheWriteInputTokens':0,'outputTokens':100,'reasoningOutputTokens':50}
        new={'inputTokens':1200,'cachedInputTokens':1000,'cacheWriteInputTokens':0,'outputTokens':20,'reasoningOutputTokens':0}
        events=[event('old',1100,old),event('new',2320,new),event('new',2320,new)]
        result=account_codex(events,'gpt-6.1-sol','new')
        self.assertEqual(result['responses'],1);self.assertEqual(result['per_response_usage'],[new])
        self.assertAlmostEqual(result['cost_usd'],.0007)

if __name__=='__main__':unittest.main()
