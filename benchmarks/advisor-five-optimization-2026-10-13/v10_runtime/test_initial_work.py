import json,unittest
from .coordinator import Coordinator

class Tests(unittest.IsolatedAsyncioTestCase):
    async def simulate(self,completed):
        events=[];actions=iter(['deliver'] if completed else ['advisor','deliver'])
        def result(cost,text):return {'status':'completed','usage':{'cost_usd':cost},'evidence':{'text':text}}
        async def worker(phase,payload,policy,cap):
            events.append(('worker',cap,policy))
            return result(.03,json.dumps({'status':'completed' if completed else 'needs_advisor','relevant_paths':['/app/draft.py'],'checks':['actual public check']}))
        async def decision(phase,payload):
            events.append(('gate',payload['stage']))
            return dict(result(.001,'gate'),decision={'action':next(actions),'issue':'remaining uncertainty','evidence':'draft and checks','paths':['/app/draft.py'],'intervention':'repair the concrete defect','acceptance':'original requirements'})
        async def advisor(phase,payload):
            events.append(('advisor',payload))
            return result(.08,json.dumps({'status':'completed','relevant_paths':['/app/draft.py']}))
        return await Coordinator(.3,'cursor/grok-4.7-low',worker,decision,advisor,lambda *args:None).run('original task'),events

    async def test_worker_completed_draft_can_deliver_without_reimplementation_or_advisor(self):
        report,events=await self.simulate(True)
        self.assertEqual(report['terminal_reason'],'deliver');self.assertEqual(report['advisor_calls'],0)
        self.assertEqual([e[0] for e in events],['worker','gate'])
        self.assertEqual(events[1][1],'delivery')
        self.assertAlmostEqual(report['cost_usd'],.031)

    async def test_partial_work_reaches_expert_with_independent_twenty_percent_cap(self):
        report,events=await self.simulate(False)
        self.assertAlmostEqual(events[0][1],.06)
        self.assertIn('Make useful progress',events[0][2])
        self.assertIn('/app/draft.py',next(e for e in events if e[0]=='advisor')[1]['worker_evidence']['text'])
        self.assertEqual(report['advisor_calls'],1);self.assertEqual(report['terminal_reason'],'deliver')

if __name__=='__main__':unittest.main()
