"""Experiments may disprove a hypothesis without losing the actual observation."""
import unittest
from .delegation import InTurnWorker,validate_work
from .test_delegation import WORK


class ExperimentTests(unittest.IsolatedAsyncioTestCase):
    async def test_unresolved_hypothesis_can_be_executed_and_disproved(self):
        work=dict(WORK,work_kind='experiment',given_facts='The original table has a numeric measurement column.',
                  hypothesis='The measurements may be strictly increasing.',
                  procedure='Read the measurement column, compare adjacent rows, save the indices of nonincreasing pairs.',
                  decision_rule='Any nonincreasing pair falsifies this hypothesis; the advisor interprets the evidence.')
        validate_work(work)
        calls=[]
        async def worker(request,cap):
            calls.append(request)
            return {'status':'preparation_observed','model_calls':1,'usage':{'cost_usd':.01},
                    'evidence':{'commands':[{'exit_code':0,'stdout':'nonincreasing_pairs=[2,4]'}]}}
        bridge=InTurnWorker(worker,lambda:.1,.04,.01,lambda *_:None)
        result=await bridge.invoke('experiment',work)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['evidence']['commands'][0]['stdout'],'nonincreasing_pairs=[2,4]')
        self.assertFalse(result['correctness_proven'])
        self.assertEqual(result['actual_cost_usd'],.01)

    async def test_missing_hypothesis_or_decision_rule_is_not_executable(self):
        async def worker(*args):raise AssertionError('No paid call for incomplete procedure')
        bridge=InTurnWorker(worker,lambda:.1,.04,.01,lambda *_:None)
        for changes in [{'work_kind':'experiment','hypothesis':''},
                        {'work_kind':'experiment','hypothesis':'test','decision_rule':''}]:
            self.assertEqual((await bridge.invoke('bad',dict(WORK,**changes)))['status'],'invalid_work_order')
        self.assertEqual(bridge.calls,{})


if __name__=='__main__':unittest.main()
