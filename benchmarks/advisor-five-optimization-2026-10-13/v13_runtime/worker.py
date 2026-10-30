"""One bounded Cursor native conversation with a remote-only custom command tool."""
import asyncio,json
from pathlib import Path
from routing import PRICES,usage_cost
from v3_runtime.recovery import recover_public_text
from .worker_contract import WorkerRelay, worker_request

async def worker_turn(folder,prompt,effort,cap,run_command,images):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    request=worker_request(prompt,effort,cap,images)
    (folder/'request.json').write_text(json.dumps(request,indent=2)+'\n')
    relay=WorkerRelay(run_command)
    with (folder/'sdk-console.txt').open('w') as log:
        process=await asyncio.create_subprocess_exec('/opt/homebrew/bin/node',str(Path(__file__).with_name('worker.mjs')),str(folder),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=log,limit=2**20)
        try:
            while line:=await process.stdout.readline():
                try:message=json.loads(line)
                except ValueError:
                    log.write(line.decode(errors='replace'));continue
                if message.get('type')!='remote_command':
                    raise RuntimeError('Unexpected SDK command relay protocol')
                result=await relay.handle(message)
                (folder/'remote-commands.json').write_text(json.dumps(relay.commands,indent=2)+'\n')
                process.stdin.write((json.dumps({'id':message['id'],'result':result})+'\n').encode())
                await process.stdin.drain()
            code=await process.wait()
        finally:
            if process.returncode is None:
                process.terminate()
                try:await asyncio.wait_for(process.wait(),10)
                except TimeoutError:process.kill();await process.wait()
        if code:
            raise RuntimeError('Cursor remote-tool SDK failed; inspect phase console')
    raw=json.loads((folder/'sdk-result.json').read_text())
    native=raw.get('usage');text,origin=recover_public_text(raw,folder/'turns'/str(len(raw['turns'])-1)/'sdk-events.jsonl')
    for key in (['inputTokens','outputTokens','cacheReadTokens','cacheWriteTokens'] if native is not None else []):
        if sum(u[key] for u in raw['usageEvents'])!=native[key]:
            raise RuntimeError('Native Cursor usage counters do not reconcile')
    result={'status':'completed' if raw['status']=='finished' else raw['status'],
        'native_model':raw['model'],'interrupted':raw.get('boundary'),
        'usage':{'native_usage':native,'cost_usd':usage_cost('grok-4.7',native,'cursor') if native else None,
                 'known_cost_lower_bound_usd':raw.get('known_cost_lower_bound_usd',0)},
        'evidence':{'text':text,'commands':relay.commands,'command_count':len(relay.commands),
                    'native_status':raw['status'],'interrupted':raw.get('boundary')},
        'native_error':raw.get('error'),'text_origin':origin}
    (folder/'phase-result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
