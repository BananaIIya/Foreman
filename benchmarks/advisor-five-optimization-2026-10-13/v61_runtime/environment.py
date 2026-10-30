"""Compact observed runtime metadata; isolated interpreter (-I) so workspace files cannot shadow stdlib modules."""
import json,shlex

SCRIPT=r'''
import configparser,importlib.metadata,json,os,pathlib,platform,shlex,sys,sysconfig,urllib.parse
installed={}
for distribution in importlib.metadata.distributions():
 name=distribution.metadata.get('Name')
 if name:installed[name.lower().replace('_','-')]=distribution.version
config=configparser.ConfigParser(interpolation=None)
config_error=None
if 'PIP_FIND_LINKS' not in os.environ or 'PIP_NO_INDEX' not in os.environ:
 try:
  config.read(['/etc/pip.conf',str(pathlib.Path.home()/'.pip/pip.conf'),str(pathlib.Path.home()/'.config/pip/pip.conf'),str(pathlib.Path(sys.prefix)/'pip.conf')]+([os.environ['PIP_CONFIG_FILE']] if os.environ.get('PIP_CONFIG_FILE') else []))
 except (OSError,configparser.Error) as exc:config_error=type(exc).__name__
def pip_setting(name,env):
 if env in os.environ:return os.environ[env]
 return config.get('install',name,fallback=config.get('global',name,fallback=''))
sources=[];wheels={};ignored_remote_sources=False
for location in shlex.split(pip_setting('find-links','PIP_FIND_LINKS'))[:4]:
 parsed=urllib.parse.urlparse(location)
 if parsed.scheme not in ('','file'):
  ignored_remote_sources=True;continue
 path=pathlib.Path(urllib.parse.unquote(parsed.path) if parsed.scheme else location)
 if not path.is_dir():continue
 # Only public distribution filenames, not file contents or arbitrary config.
 sources.append(str(path))
 for file in sorted(path.glob('*.whl'))[:200]:
  parts=file.name.split('-')
  if len(parts)>=5:wheels.setdefault(parts[0].lower().replace('_','-'),set()).add(parts[1])
workspace=[]
for root,dirs,files in os.walk('/app',followlinks=False):
 dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d not in ('node_modules','__pycache__','site-packages'))
 for name in sorted(files):
  if name.startswith('.'):continue
  path=pathlib.Path(root)/name
  try:workspace.append({'path':str(path),'bytes':path.lstat().st_size,'symlink':path.is_symlink()})
  except OSError:continue
  if len(workspace)>=160:break
 if len(workspace)>=160:break
limit=100
print(json.dumps({'python_version':platform.python_version(),'python_executable':sys.executable,'architecture':platform.machine(),
 'python_in_virtual_environment':sys.prefix!=sys.base_prefix,
 'python_externally_managed':(pathlib.Path(sysconfig.get_path('stdlib'))/'EXTERNALLY-MANAGED').exists() and sys.prefix==sys.base_prefix,
 'task_workspace_files':workspace,'workspace_listing_limited':len(workspace)>=160,
 'installed_python_packages':dict(sorted(installed.items())[:limit]),
 'installed_package_count':len(installed),'installed_list_truncated':len(installed)>limit,
 'local_wheel_sources':sources,'local_wheel_versions':{k:sorted(v) for k,v in sorted(wheels.items())[:limit]},
 'wheel_list_truncated':len(wheels)>limit,
 'pip_no_index':pip_setting('no-index','PIP_NO_INDEX').lower() in ('1','true','yes'),
 'pip_config_metadata_error':config_error,
 'remote_package_source_configured':ignored_remote_sources,
 'note':'Observed metadata at this checkpoint. Workspace file names/sizes only, no file contents. Wheel filenames do not prove compatibility; use Python/architecture and real execution. Hidden tests, secret values and remote package URLs are excluded.'}))
'''

async def environment_manifest(agent,environment):
    result=await agent.exec_as_agent(environment,command='python3 -I -c '+shlex.quote(SCRIPT),cwd='/app',timeout_sec=30)
    if result.return_code:
        return {'available':False,'reason':'Runtime metadata command returned nonzero','return_code':result.return_code}
    return json.loads(result.stdout)
