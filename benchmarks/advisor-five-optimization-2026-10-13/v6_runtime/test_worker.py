import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from v6_runtime.worker import bounded_worker

def answer(status='commands',commands=None,cost=.01):
    return {'status':'completed','usage':{'cost_usd':cost,'native_usage':{'inputTokens':100,'outputTokens':10,'cacheReadTokens':20,'cacheWriteTokens':0} if cost is not None else None},
        'patch':{'status':status,'commands':commands or [],'report':{'summary':'current evidence','relevant_paths':['/app/input']}},'evidence':{'text':'public answer'}}

class Tests(unittest.IsolatedAsyncioTestCase):
    async def simulate(self,replies,cap=.2,fail=False):
        sequence=iter(replies);calls=[];commands=[]
        async def native(folder,prompt,effort):
            calls.append(json.loads(prompt.split('\n',1)[1]));return next(sequence)
        async def run(command):
            commands.append(command);return SimpleNamespace(return_code=9 if fail else 0,stdout='real observation',stderr='failure' if fail else '')
        with tempfile.TemporaryDirectory() as t:
            r=await bounded_worker(Path(t)/'worker-discovery','original task','low',cap,run,native)
        return r,calls,commands

    async def test_actual_observations_returned_and_all_calls_charged(self):
        r,c,cmd=await self.simulate([answer(commands=['inspect']),answer('needs_advisor')])
        self.assertEqual(cmd,['inspect']);self.assertEqual(len(c),2)
        self.assertIn('real observation',json.dumps(c[1]['current_phase_history']))
        self.assertEqual(r['usage']['cost_usd'],.02)
        self.assertEqual(r['usage']['native_usage']['inputTokens'],200)

    async def test_more_commands_than_limit_never_execute(self):
        r,c,cmd=await self.simulate([answer(commands=['one','two','three'])])
        self.assertFalse(cmd);self.assertEqual(r['status'],'controller_command_limit')

    async def test_model_cannot_loop_after_cost_boundary(self):
        r,c,cmd=await self.simulate([answer(commands=['inspect'],cost=.04)],cap=.03)
        self.assertEqual(len(c),1);self.assertEqual(r['status'],'controller_cost_boundary')
        self.assertEqual(r['usage']['cost_usd'],.04)

    async def test_missing_usage_is_unknown_and_stops_more_inference(self):
        r,c,cmd=await self.simulate([answer(commands=['inspect'],cost=None)])
        self.assertIsNone(r['usage']['cost_usd']);self.assertEqual(len(c),1)

    async def test_max_calls_even_for_repeated_invalid_response(self):
        bad=answer();bad['patch']=None
        r,c,cmd=await self.simulate([bad,bad,bad])
        self.assertEqual(len(c),3);self.assertFalse(cmd);self.assertEqual(r['usage']['cost_usd'],.03)

    async def test_failed_command_stops_batch_and_returns_real_failure(self):
        r,c,cmd=await self.simulate([answer(commands=['fail','not-run']),answer('needs_advisor')],fail=True)
        self.assertEqual(cmd,['fail'])
        self.assertEqual(r['evidence']['commands'][0]['exit_code'],9)
        self.assertIn('failure',json.dumps(c[1]['current_phase_history']))

if __name__=='__main__':unittest.main()
