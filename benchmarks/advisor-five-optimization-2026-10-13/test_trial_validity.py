import json,tempfile,unittest
from pathlib import Path
from trial_validity import startup_provider_failure

class Tests(unittest.TestCase):
    def fixture(self,root,text='',status='error'):
        phase=Path(root)/'agent/phases/worker-discovery';phase.mkdir(parents=True)
        (phase/'request.json').write_text('{}')
        sub=phase/'rounds/0';sub.mkdir(parents=True)
        (sub/'phase-result.json').write_text(json.dumps({'status':status,'native_error':{'message':'resource_exhausted'} if status=='error' else None,'evidence':{'text':text,'command_count':0}}))
    def test_empty_provider_failure_is_not_a_model_answer(self):
        with tempfile.TemporaryDirectory() as root:
            self.fixture(root)
            self.assertEqual(startup_provider_failure(root)['kind'],'provider_failed_before_any_worker_answer')
    def test_partial_answer_is_not_discarded_by_startup_rule(self):
        with tempfile.TemporaryDirectory() as root:
            self.fixture(root,text='actual public model answer')
            self.assertIsNone(startup_provider_failure(root))
    def test_completed_empty_model_reply_is_not_provider_failure(self):
        with tempfile.TemporaryDirectory() as root:
            self.fixture(root,status='completed')
            self.assertIsNone(startup_provider_failure(root))

if __name__=='__main__':unittest.main()
