import asyncio,json,tempfile,shutil,ast
from pathlib import Path
from hybrid_native import Session
from shared_workspace import make_home
from hybrid_agent import parse_json
from v2_runtime.policy import ADVISOR,PATCH_SCHEMA,validate_patch
ROOT=Path(__file__).resolve().parent.parent
async def main():
 out=ROOT/'raw/preflight/sol-no-tools-patch';out.mkdir(parents=True,exist_ok=True)
 (out/'launch-once.json').open('x').write('{"probe":"sol-native-patch"}\n')
 private=Path(tempfile.mkdtemp(prefix='sol-patch-probe-',dir='/private/tmp'))
 home=make_home(private,'home','gpt-6.1-sol','high');work=private/'empty';work.mkdir()
 client=Session(home,out,'gpt-6.1-sol','high')
 try:
  await client.start()
  r=await client.turn(None,'Transport probe only. Return a patch for /app/totals.py. Current source: def sum_values(values):\n    total = 0\n    for value in values:\n        total = value\n    return total\nFix to sum values, including empty inputs and negative values. Only define this function, no imports or side effects. No tools.',ADVISOR,str(work),schema=PATCH_SCHEMA,no_tools=True)
  p=parse_json(r['evidence']['text']);validate_patch(p)
  assert len(p['files'])==1
  source=p['files'][0]['content'];tree=ast.parse(source)
  assert len(tree.body)==1 and isinstance(tree.body[0],ast.FunctionDef)
  allowed=(ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Return,ast.Assign,ast.AugAssign,ast.For,ast.Name,ast.Load,ast.Store,ast.Constant,ast.BinOp,ast.Add,ast.Call)
  assert all(isinstance(n,allowed) for n in ast.walk(tree))
  assert all(isinstance(n.func,ast.Name) and n.func.id=='sum' for n in ast.walk(tree) if isinstance(n,ast.Call))
  ns={'__builtins__':{'sum':sum}};exec(compile(tree,'probe','exec'),ns)
  for values,expected in [([],0),([1,2],3),([-3,1],-2),([4],4)]:assert ns['sum_values'](values)==expected
  audit={'status':'passed','model':'gpt-6.1-sol','effort':'high','cases':4,'cost_usd':r['usage']['cost_usd'],'benchmark_result':False}
  (out/'audit.json').write_text(json.dumps(audit,indent=2)+'\n');print(json.dumps(audit))
 finally:await client.close();shutil.rmtree(private)
if __name__=='__main__':asyncio.run(main())
