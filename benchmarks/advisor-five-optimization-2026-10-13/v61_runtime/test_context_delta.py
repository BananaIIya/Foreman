import copy,json,unittest
from .context_delta import resume_delta,restore_evidence


class ContextDeltaTests(unittest.TestCase):
    def setUp(self):
        self.known={'text':'Original uncertainty retained in native report',
                    'commands':[{'command':'original check','exit_code':3,'output':'observed failure'}],
                    'native_status':'interrupted','interrupted':'budget_boundary_exceeded'}
        self.memory={'thread_id':'same-thread','turn_id':'prior-turn','phase':'advisor-0',
                     'compacted':False,'evidence':self.known}

    def test_exact_direct_evidence_round_trip_and_new_contract_preserved(self):
        payload={'worker_evidence':self.known,'contract':{'issue':'New concrete issue'},'cost_target_usd':.03}
        original=copy.deepcopy(payload)
        result,receipt=resume_delta(payload,self.memory,'same-thread')
        self.assertEqual(payload,original)
        self.assertEqual(result['contract'],payload['contract'])
        self.assertEqual(result['worker_evidence']['interrupted'],'budget_boundary_exceeded')
        self.assertEqual(restore_evidence(result['worker_evidence'],receipt,self.known),self.known)

    def test_nested_expert_duplicate_removed_but_new_failures_and_checks_retained(self):
        report={'expert_evidence':self.known,'checks':[{'command':'new export','return_code':7,'stderr':'new failure'}],
                'unexecuted_commands':[{'command':'check original requirement'}],'all_commands_succeeded':False}
        payload={'worker_evidence':{'text':json.dumps(report,ensure_ascii=False)}}
        result,receipt=resume_delta(payload,self.memory,'same-thread')
        current=json.loads(result['worker_evidence']['text'])
        self.assertEqual(current['checks'],report['checks'])
        self.assertEqual(current['unexecuted_commands'],report['unexecuted_commands'])
        self.assertFalse(current['all_commands_succeeded'])
        self.assertEqual(restore_evidence(result['worker_evidence'],receipt,self.known),payload['worker_evidence'])

    def test_other_threads_compacted_context_or_changed_evidence_not_omitted(self):
        payload={'worker_evidence':copy.deepcopy(self.known)}
        self.assertIs(resume_delta(payload,self.memory,'different-thread')[0],payload)
        self.assertIs(resume_delta(payload,dict(self.memory,compacted=True),'same-thread')[0],payload)
        payload['worker_evidence']['commands'][0]['output']='different actual observation'
        self.assertIs(resume_delta(payload,self.memory,'same-thread')[0],payload)
        self.assertIs(resume_delta(payload,None,'same-thread')[0],payload)

    def test_noncanonical_prose_and_pretty_json_preserved(self):
        for text in ['not JSON',json.dumps({'expert_evidence':self.known},indent=2)]:
            payload={'worker_evidence':{'text':text}}
            self.assertIs(resume_delta(payload,self.memory,'same-thread')[0],payload)


if __name__=='__main__':unittest.main()
