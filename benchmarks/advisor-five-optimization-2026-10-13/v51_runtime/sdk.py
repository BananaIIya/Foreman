"""One bounded Cursor native conversation with a remote-only custom command tool."""
import asyncio,json
from pathlib import Path
from routing import PRICES,usage_cost
from v3_runtime.recovery import recover_public_text
from v9_runtime.relay import CommandRelay
from .live_review import allowance, feedback, known_cost, REVIEW_THRESHOLD
from .delegation import InTurnWorker, TOOL

async def advisor_turn(folder,prompt,effort,cap,run_command,live_reviewer=None,routine_worker=None,worker_limit=0.,worker_reserve=0.):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    prices=dict(zip(['input','cache','write','output'],[float(x) if x is not None else None for x in PRICES['grok-4.7']]))
    request={'model':'grok-4.7','provider':'cursor-sdk-remote-tools','effort':effort,'prompt':prompt,
        'tools':['mcp'],'custom_tools':['remote_command'],'setting_sources':[],'mcp_servers':{},
        'budget_usd':cap,'live_review_allowance_usd':allowance(cap) if live_reviewer else 0.,'live_review_threshold':REVIEW_THRESHOLD,'command_limit':12,'native_turn_limit':6,'token_prices_per_million':prices,'sdk_version':'1.0.37'}
    request.update(in_turn_worker_enabled=routine_worker is not None,
                   in_turn_worker_limit_usd=worker_limit,in_turn_worker_continuation_reserve_usd=worker_reserve,
                   in_turn_worker_tool=TOOL,budget_includes_in_turn_worker=True)
    if routine_worker is not None:request['custom_tools'].append('routine_worker')
    (folder/'request.json').write_text(json.dumps(request,indent=2)+'\n')
    live_result=None;review_seen=False
    worker_remaining=None
    def worker_event(name,value):
        with (folder/'in-turn-worker.events.jsonl').open('a') as stream:
            stream.write(json.dumps({'event':name,**value})+'\n')
    bridge=InTurnWorker(routine_worker,lambda:worker_remaining,worker_limit,worker_reserve,worker_event) if routine_worker else None
    relay=CommandRelay(run_command,request['command_limit'])
    with (folder/'sdk-console.txt').open('w') as log:
        process=await asyncio.create_subprocess_exec('/opt/homebrew/bin/node',str(Path(__file__).with_name('advisor.mjs')),str(folder),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=log,limit=2**20)
        try:
            while line:=await process.stdout.readline():
                try:message=json.loads(line)
                except ValueError:
                    log.write(line.decode(errors='replace'));continue
                if message.get('type')=='routine_worker':
                    if bridge is None:raise RuntimeError('Unconfigured routine worker')
                    worker_remaining=message['remaining_before_worker_usd']
                    result=await bridge.invoke(message['id'],message.get('arguments'))
                    process.stdin.write((json.dumps({'id':message['id'],'result':result})+'\n').encode())
                    await process.stdin.drain()
                    continue
                if message.get('type')=='live_review':
                    if review_seen:raise RuntimeError('Duplicate live reviewer request')
                    review_seen=True
                    evidence={'text':message.get('public_checkpoint',''),'commands':list(relay.commands),
                              'command_count':len(relay.commands)}
                    if bridge:evidence['in_turn_worker_evidence']=[r['result'].get('evidence',{}) for r in bridge.records]
                    (folder/'live-review-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
                    if live_reviewer is None:raise RuntimeError('Unconfigured live reviewer')
                    try:live_result=await live_reviewer(evidence,message['spent_usd'],message['remaining_usd'],request['live_review_allowance_usd'])
                    except Exception as exc:
                        live_result={'status':'review_failed','usage':{'cost_usd':None},'evidence':{'text':''},'review_error_type':type(exc).__name__}
                    fee=known_cost(live_result)
                    result={'review_cost_usd':fee,'feedback':feedback(live_result.get('review'))}
                    receipt={'attempts':1,'review_cost_usd':fee,'same_native_conversation':True,
                             'feedback':result['feedback'],'grader_feedback':False,'review_cost_separate_from_native_usage':True}
                    (folder/'live-review-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
                    process.stdin.write((json.dumps({'id':message['id'],'result':result})+'\n').encode())
                    await process.stdin.drain()
                    continue
                if message.get('type')!='remote_command':
                    raise RuntimeError('Unexpected SDK command relay protocol')
                result=await relay.handle(message)
                (folder/'remote-commands.json').write_text(json.dumps(relay.commands,indent=2)+'\n')
                process.stdin.write((json.dumps({'id':message['id'],'result':result})+'\n').encode())
                await process.stdin.drain()
            code=await process.wait()
        finally:
            if bridge is not None:
                await bridge.settle()
                (folder/'in-turn-worker-receipt.json').write_text(json.dumps({
                    'same_native_conversation':True,'records':bridge.records,
                    'cost_usd':bridge.spent if bridge.usage_complete else None,
                    'usage_complete':bridge.usage_complete,'native_usage_excludes_worker':True,
                    'budget_measurement':'Cursor expert usage at completed native turn boundaries; all overshoot charged',
                    'grader_feedback':False},indent=2)+'\n')
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
        'native_error':raw.get('error'),'text_origin':origin,'live_review':live_result,
        'in_turn_workers':[r['result'] for r in bridge.records] if bridge else []}
    result['evidence']['in_turn_worker_evidence']=[r.get('evidence',{}) for r in result['in_turn_workers']]
    (folder/'phase-result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
