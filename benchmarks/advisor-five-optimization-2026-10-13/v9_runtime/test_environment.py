import contextlib,io,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from v9_runtime.environment import SCRIPT

class Tests(unittest.TestCase):
    def test_only_public_package_metadata_and_local_wheel_filenames(self):
        with tempfile.TemporaryDirectory() as t:
            wheel=Path(t)/'example_lib-1.2.3-py3-none-any.whl';wheel.write_bytes(b'not read')
            (Path(t)/'secret.env').write_text('SECRET_SENTINEL')
            fake=SimpleNamespace(metadata={'Name':'Example_Lib'},version='1.0')
            env={'PIP_FIND_LINKS':t+' https://username:secret@private.example/simple','PIP_NO_INDEX':'1','API_KEY':'SECRET_SENTINEL'}
            output=io.StringIO()
            with patch.dict(os.environ,env,clear=True),patch('importlib.metadata.distributions',return_value=[fake]),contextlib.redirect_stdout(output):
                exec(SCRIPT,{})
            raw=output.getvalue();data=json.loads(raw)
            self.assertEqual(data['installed_python_packages'],{'example-lib':'1.0'})
            self.assertEqual(data['local_wheel_versions'],{'example-lib':['1.2.3']})
            self.assertTrue(data['pip_no_index']);self.assertTrue(data['remote_package_source_configured'])
            self.assertNotIn('SECRET_SENTINEL',raw);self.assertNotIn('username',raw);self.assertNotIn('private.example',raw)

    def test_empty_inventory_is_observed_not_assumed_missing_dependency(self):
        output=io.StringIO()
        with patch.dict(os.environ,{},clear=True),patch('configparser.ConfigParser.read',return_value=[]),patch('importlib.metadata.distributions',return_value=[]),contextlib.redirect_stdout(output):exec(SCRIPT,{})
        data=json.loads(output.getvalue())
        self.assertEqual(data['local_wheel_versions'],{})
        self.assertFalse(data['pip_no_index']);self.assertEqual(data['installed_package_count'],0)

    def test_pip_configuration_cache_is_discovered_without_environment_override(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t)/'available_lib-9.1-py3-none-any.whl').touch()
            def read(config,paths):
                config.read_string('[global]\nno-index=true\nfind-links='+t+'\nindex-url=https://user:SECRET@private.example/simple\n')
            output=io.StringIO()
            with patch.dict(os.environ,{},clear=True),patch('configparser.ConfigParser.read',read),patch('importlib.metadata.distributions',return_value=[]),contextlib.redirect_stdout(output):exec(SCRIPT,{})
            data=json.loads(output.getvalue())
            self.assertTrue(data['pip_no_index'])
            self.assertEqual(data['local_wheel_versions'],{'available-lib':['9.1']})
            self.assertNotIn('SECRET',output.getvalue());self.assertNotIn('private.example',output.getvalue())

if __name__=='__main__':unittest.main()
