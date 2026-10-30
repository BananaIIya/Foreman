"""Continuous native expert with one asynchronous, fully charged public review."""
import asyncio
import copy
import json
from hybrid_native import account_codex, read_events, public_evidence
from v40_runtime.native import AdvisorSession as PreviousSession
from .live_review import allowance, feedback, known_cost, REVIEW_THRESHOLD
from .client_tools import ClientToolsMixin
from .delegation import InTurnWorker, TOOL


class AdvisorSession(ClientToolsMixin, PreviousSession):
    live_reviewer = None
    routine_worker = None
    routine_worker_limit = 0.
    routine_worker_reserve = 0.

    async def turn(self, url, prompt, policy, cwd, thread_id=None, schema=None,
                   extra_input=None, budget_usd=None, no_tools=False):
        if no_tools or (self.live_reviewer is None and self.routine_worker is None) or budget_usd is None:
            return await super().turn(url,prompt,policy,cwd,thread_id,schema,extra_input,budget_usd,no_tools)
        bound = asyncio.Event()
        bridge = None
        thread_id_bound = None
        turn_id = None
        finished = False
        interrupted = None
        spend = 0.
        worker_budget_observations=[]
        def worker_event(name, value):
            with (self.log_dir/'in-turn-worker.events.jsonl').open('a') as stream:
                stream.write(json.dumps({'event':name,**value})+'\n')
        def current_remaining():
            # Read actual native usage, including events queued before this tool request.
            measured = account_codex(read_events(self.log_dir/'app-server.events.jsonl'),self.model,turn_id)['cost_usd']
            review_fee = known_cost(review_result) if review_processed else reserve
            # App-server can defer this response's usage until AFTER the tool reply.
            # The allocation is a boundary estimate, not a claim of zero native cost.
            lower_bound=spend if measured is None else max(spend,measured)
            worker_budget_observations.append({'native_cost_usd':measured,
                'known_native_lower_bound_usd':lower_bound,'inflight_response_cost_may_be_pending':True,
                'continuation_reserve_usd':self.routine_worker_reserve})
            return None if review_fee is None else max(0.,budget_usd-lower_bound-review_fee)
        async def handle_worker(params):
            await bound.wait()
            if (params.get('tool')!='routine_worker' or params.get('namespace') not in (None,'')):
                return {'status':'unknown_tool','model_calls':0}
            if (params.get('threadId')!=thread_id_bound or params.get('turnId')!=turn_id
                    or finished or interrupted):
                return {'status':'wrong_native_session','model_calls':0}
            result = await bridge.invoke(params.get('callId'),params.get('arguments'))
            measured = account_codex(read_events(self.log_dir/'app-server.events.jsonl'),self.model,turn_id)['cost_usd']
            effective = cap()
            if effective is None:
                await interrupt('unknown_delegated_worker_usage')
            elif measured is not None and measured>effective:
                await interrupt('combined_worker_budget_exceeded')
            return result
        if self.routine_worker is not None:
            bridge = InTurnWorker(self.routine_worker,current_remaining,self.routine_worker_limit,
                                  self.routine_worker_reserve,worker_event)
            self.configure_client_tools([TOOL],handle_worker)
        env=[]
        if url:
            await self.rpc('environment/add',{'environmentId':'daytona','execServerUrl':url,'connectTimeoutMs':30000})
            env=[{'environmentId':'daytona','cwd':cwd,'runtimeWorkspaceRoots':[cwd]}]
        params={'model':self.model,'approvalPolicy':'never','cwd':cwd,'sandbox':'danger-full-access' if url else 'read-only',
                'environments':env,'config':{'model_reasoning_effort':self.effort,'service_tier':'default',
                   'features':{'unified_exec':True,'multi_agent':False},'web_search':'disabled'}}
        if thread_id:
            params['threadId']=thread_id;t=await self.rpc('thread/resume',params)
        else:
            params.update(allowProviderModelFallback=False,experimentalRawEvents=True,developerInstructions=policy)
            t=await self.rpc('thread/start',params);thread_id=t['thread']['id']
        assert t['model']==self.model and t['reasoningEffort']==self.effort
        thread_id_bound = thread_id
        turn_params={'threadId':thread_id,'effort':self.effort,'cwd':cwd,'environments':env,
                     'input':[{'type':'text','text':prompt,'text_elements':[]}]+(extra_input or [])}
        if schema:turn_params['outputSchema']=schema
        reserve=allowance(budget_usd) if self.live_reviewer is not None else 0.
        (self.log_dir/'request.json').write_text(json.dumps({'model':self.model,'effort':self.effort,
            'thread_id':thread_id,'prompt':prompt,'policy':policy,'budget_usd':budget_usd,
            'remote_executor':bool(url),'no_tools':False,'live_review_allowance_usd':reserve,
            'live_review_threshold':REVIEW_THRESHOLD,'budget_includes_live_review':True,
            'in_turn_worker_enabled':bridge is not None,'in_turn_worker_limit_usd':self.routine_worker_limit,
            'in_turn_worker_continuation_reserve_usd':self.routine_worker_reserve,
            'budget_includes_in_turn_worker':True},indent=2)+'\n')
        started=await self.rpc('turn/start',turn_params);turn_id=started['turn']['id']
        observations=[];interrupted=None;notice_sent=False;review_task=None;review_result=None
        review_started=False;review_processed=False;finished=False;status=None;spend=0.
        next_event=asyncio.create_task(self.notifications.get())
        receipt={'threshold':REVIEW_THRESHOLD,'allowance_usd':reserve,'attempts':0,'same_thread':thread_id,
                 'same_turn':turn_id,'grader_feedback':False,'review_cost_separate_from_native_usage':True}
        def cap():
            cost=known_cost(review_result) if review_processed else reserve
            worker_cost=bridge.committed if bridge else 0.
            return None if cost is None or worker_cost is None else max(0.,budget_usd-cost-worker_cost)
        async def interrupt(reason):
            nonlocal interrupted
            if interrupted is not None or finished:return
            interrupted=reason
            try:await self.rpc('turn/interrupt',{'threadId':thread_id,'turnId':turn_id})
            except RuntimeError:pass  # Natural completion can race; do not restart.
        async def consume_review(allow_steer):
            nonlocal review_result,review_processed
            if review_processed:return
            try:review_result=await review_task
            except Exception as exc:
                review_result={'status':'review_failed','usage':{'cost_usd':None},
                               'evidence':{'text':''},'review_error_type':type(exc).__name__}
            review_processed=True
            receipt.update(review_status=review_result['status'],review_cost_usd=known_cost(review_result))
            remaining_cap=cap()
            if remaining_cap is None:
                receipt['feedback_status']='unknown_review_usage';await interrupt('unknown_review_usage');return
            if spend>remaining_cap:
                receipt['feedback_status']='combined_budget_exhausted';await interrupt('budget_boundary_exceeded');return
            message=feedback(review_result.get('review'))
            if not message:
                receipt['feedback_status']='no_grounded_change_requested';return
            if not allow_steer or finished or interrupted:
                receipt['feedback_status']='turn_already_terminal';return
            try:
                result=await self.rpc('turn/steer',{'threadId':thread_id,'expectedTurnId':turn_id,
                    'input':[{'type':'text','text':message,'text_elements':[]}]})
                receipt.update(feedback_status='accepted',feedback=message,response=result)
            except (RuntimeError,TimeoutError) as exc:
                receipt.update(feedback_status='not_confirmed',feedback_error_type=type(exc).__name__)
        bound.set()
        try:
            while not finished:
                waiters={next_event}
                if review_task is not None and not review_processed:waiters.add(review_task)
                ready,_=await asyncio.wait(waiters,timeout=1800,return_when=asyncio.FIRST_COMPLETED)
                if not ready:raise TimeoutError('No native observation during active intervention')
                # Consume current usage/completion before steering a concurrently ready review.
                if next_event in ready:
                    e=next_event.result();observations.append(e)
                    if e.get('method','').startswith('transport/'):
                        raise RuntimeError('Native transport closed during active intervention')
                    if e.get('method')=='turn/completed' and e['params']['turn']['id']==turn_id:
                        finished=True;status=e['params']['turn']['status']
                    if e.get('method')=='thread/tokenUsage/updated' and interrupted is None:
                        measured=account_codex(observations,self.model,turn_id)['cost_usd']
                        if measured is not None:spend=measured
                        effective=cap()
                        if effective is None:await interrupt('unknown_review_usage')
                        elif measured is not None and spend>effective:await interrupt('budget_boundary_exceeded')
                        elif measured is not None:
                            if self.live_reviewer is not None and not review_started and spend>=budget_usd*REVIEW_THRESHOLD:
                                review_started=True;receipt['attempts']=1
                                snapshot=copy.deepcopy(public_evidence(observations))
                                if bridge:
                                    snapshot['in_turn_worker_evidence']=[copy.deepcopy(r['result'].get('evidence',{})) for r in bridge.records]
                                receipt['expert_spend_at_review_start_usd']=spend
                                (self.log_dir/'live-review-evidence.json').write_text(json.dumps(snapshot,indent=2)+'\n')
                                review_task=asyncio.create_task(self.live_reviewer(snapshot,spend,effective-spend,reserve))
                            if spend>=effective*.75 and not notice_sent:
                                notice_sent=True;await self.budget_notice(thread_id,turn_id,spend,effective)
                    if not finished:next_event=asyncio.create_task(self.notifications.get())
                if review_task is not None and review_task.done() and not review_processed:
                    await consume_review(not finished)
        finally:
            finished = True
            if bridge is not None:
                await bridge.settle()
                await self.settle_client_tools()
                (self.log_dir/'in-turn-worker-receipt.json').write_text(json.dumps({
                    'same_thread':thread_id,'same_turn':turn_id,'records':bridge.records,
                    'cost_usd':bridge.spent if bridge.usage_complete else None,
                    'known_cost_lower_bound_usd':bridge.spent,'usage_complete':bridge.usage_complete,
                    'native_usage_excludes_worker':True,'grader_feedback':False,
                    'budget_observations':worker_budget_observations,
                    'budget_measurement':'Observed native response boundaries; tool-call response usage may arrive after tool reply. All overshoot charged.'},indent=2)+'\n')
            if not next_event.done():
                next_event.cancel()
                try:await next_event
                except asyncio.CancelledError:pass
            # A started paid review is never discarded or retried if the expert ends first.
            if review_task is not None and not review_processed:await consume_review(False)
            (self.log_dir/'live-review-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        events=read_events(self.log_dir/'app-server.events.jsonl')
        result={'thread_id':thread_id,'turn_id':turn_id,'status':status,'interrupted':interrupted,
                'usage':account_codex(events,self.model,turn_id),'evidence':public_evidence(events),
                  'live_review':review_result,'live_review_receipt':receipt,
                  'in_turn_workers':[r['result'] for r in bridge.records] if bridge else []}
        result['evidence']['in_turn_worker_evidence']=[copy.deepcopy(r.get('evidence',{})) for r in result['in_turn_workers']]
        (self.log_dir/'phase-result.json').write_text(json.dumps(result,indent=2)+'\n')
        return result
