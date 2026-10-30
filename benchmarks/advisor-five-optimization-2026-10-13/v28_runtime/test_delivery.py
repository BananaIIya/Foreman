import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from .delivery import execute_delivery, validate_manifest

MANIFEST={'version':2,'scope':'task','repeat_after_advisor':True,'token':'this-task',
          'commands':['generate from current data'],'check_commands':['check generated output'],
          'artifact_paths':['/app/output']}


class RepeatDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_authorization_identity_and_bounds_required(self):
        self.assertIs(validate_manifest(MANIFEST,'this-task'),MANIFEST)
        for changed in [{'repeat_after_advisor':False},{'token':'other-task'},{'scope':'global'},
                        {'version':True},{'version':1},{'commands':[]},{'extra':'unsupported'},
                        {'artifact_paths':['/app/../outside']}]:
            with self.assertRaises(ValueError):validate_manifest(dict(MANIFEST,**changed),'this-task')

    async def test_second_intervention_materializes_latest_data_without_model(self):
        state={'data':1,'artifact':None};calls=[]
        async def run(command):
            calls.append(command)
            if command==MANIFEST['commands'][0]:state['artifact']=state['data']
            return SimpleNamespace(return_code=0 if state['artifact']==state['data'] else 2,stdout='checked',stderr='')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            first=await execute_delivery(root/'executor-0',MANIFEST,'this-task',run,{'text':'Initial implemented pipeline'})
            self.assertEqual(state['artifact'],1)
            state['data']=2  # Expert correction completed before its model budget stop.
            second=await execute_delivery(root/'executor-1',MANIFEST,'this-task',run,{'text':'Corrected data'})
            self.assertEqual(state['artifact'],2)
            self.assertEqual(second['execution']['previous_confirmed_executions'],['executor-0'])
            self.assertTrue(first['execution']['all_commands_succeeded'])
            self.assertTrue(second['execution']['all_commands_succeeded'])
            self.assertEqual(second['usage']['cost_usd'],0.)
            self.assertTrue(second['no_model_call'])
            self.assertEqual(calls,(MANIFEST['commands']+MANIFEST['check_commands'])*2)
            with self.assertRaises(FileExistsError):await execute_delivery(root/'executor-1',MANIFEST,'this-task',run,{})
            self.assertEqual(len(calls),4)

    async def test_uncertain_previous_delivery_is_never_replayed(self):
        calls=[]
        async def run(command):
            calls.append(command)
            raise RuntimeError('transport disappeared after dispatch')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaisesRegex(RuntimeError,'transport disappeared'):
                await execute_delivery(root/'executor-0',MANIFEST,'this-task',run,{})
            with self.assertRaisesRegex(RuntimeError,'completion is unknown'):
                await execute_delivery(root/'executor-1',MANIFEST,'this-task',run,{})
            self.assertEqual(len(calls),1)

    async def test_confirmed_failure_is_retained_and_stops_checks(self):
        async def run(command):return SimpleNamespace(return_code=4,stdout='partial',stderr='original error')
        with tempfile.TemporaryDirectory() as folder:
            result=await execute_delivery(Path(folder)/'executor-0',MANIFEST,'this-task',run,{})
            self.assertFalse(result['execution']['all_commands_succeeded'])
            self.assertEqual(result['execution']['checks'][0]['stderr'],'original error')
            self.assertEqual(len(result['execution']['unexecuted_commands']),1)
            self.assertEqual(json.loads((Path(folder)/'executor-0/execution-result.json').read_text()),result)


if __name__=='__main__':unittest.main()
