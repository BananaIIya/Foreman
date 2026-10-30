"""One measured cost warning before the unchanged native hard budget stop."""
import asyncio
import json
from hybrid_native import account_codex, read_events, public_evidence
from v17_runtime.native import AdvisorSession as BoundedAdvisorSession


class AdvisorSession(BoundedAdvisorSession):
    async def budget_notice(self, thread_id, turn_id, spend, cap):
        text = (f'Execution controller cost checkpoint: this intervention has used ${spend:.6f} '
                f'of its ${cap:.6f} model allowance; approximately ${max(0,cap-spend):.6f} remains. '
                'The existing hard cost boundary is unchanged. Prioritize finishing and checking the required artifact '
                'from the work already implemented. Publish the exact mechanical delivery manifest supplied in the '
                'original contract as soon as its commands are runnable. Avoid optional investigation, previews, or '
                'repeating input inspection. Preserve uncertainty and actual errors; do not fabricate missing answers '
                'or claim correctness merely to meet the budget. This is measured usage feedback, not grader feedback.')
        receipt = {'spent_usd': spend, 'cap_usd': cap, 'threshold': .75, 'thread_id': thread_id,
                   'turn_id': turn_id, 'message': text, 'attempts': 1, 'hard_stop_unchanged': True}
        try:
            result = await self.rpc('turn/steer', {'threadId': thread_id, 'expectedTurnId': turn_id,
                                                'input': [{'type': 'text', 'text': text, 'text_elements': []}]})
            receipt.update(status='accepted', response=result)
        except (RuntimeError, TimeoutError) as exc:
            # A turn may finish concurrently. Do not replay or start another turn.
            receipt.update(status='not_confirmed', error_type=type(exc).__name__)
        (self.log_dir / 'budget-notice.json').write_text(json.dumps(receipt, indent=2) + '\n')

    async def turn(self, url, prompt, policy, cwd, thread_id=None, schema=None,
                   extra_input=None, budget_usd=None, no_tools=False):
        env = []
        if url:
            await self.rpc('environment/add', {'environmentId': 'daytona', 'execServerUrl': url, 'connectTimeoutMs': 30000})
            env = [{'environmentId': 'daytona', 'cwd': cwd, 'runtimeWorkspaceRoots': [cwd]}]
        params = {
            'model': self.model, 'approvalPolicy': 'never', 'cwd': cwd,
            'sandbox': 'danger-full-access' if url else 'read-only',
            'environments': env,
            'config': {'model_reasoning_effort': self.effort, 'service_tier': 'default',
                       'features': {'unified_exec': True, 'multi_agent': False}, 'web_search': 'disabled'},
        }
        if thread_id:
            params['threadId'] = thread_id
            t = await self.rpc('thread/resume', params)
        else:
            params.update(allowProviderModelFallback=False, experimentalRawEvents=True,
                          developerInstructions=policy)
            t = await self.rpc('thread/start', params)
            thread_id = t['thread']['id']
        assert t['model'] == self.model and t['reasoningEffort'] == self.effort
        turn_params = {'threadId': thread_id, 'effort': self.effort, 'cwd': cwd,
                       'environments': env,
                       'input': [{'type': 'text', 'text': prompt, 'text_elements': []}] + (extra_input or [])}
        if schema:
            turn_params['outputSchema'] = schema
        (self.log_dir / 'request.json').write_text(json.dumps({
            'model': self.model, 'effort': self.effort, 'thread_id': thread_id,
            'prompt': prompt, 'policy': policy, 'budget_usd': budget_usd,
            'remote_executor': bool(url), 'no_tools': no_tools}, indent=2) + '\n')
        started = await self.rpc('turn/start', turn_params)
        turn_id = started['turn']['id']
        observations = []
        interrupted = None
        budget_notice_sent = False
        while True:
            e = await asyncio.wait_for(self.notifications.get(), 1800)
            observations.append(e)
            if e.get('method', '').startswith('transport/'):
                raise RuntimeError('Native Codex transport closed during active turn')
            if e.get('method') == 'item/started' and no_tools:
                kind = e['params']['item']['type']
                if kind not in ('agentMessage', 'reasoning', 'userMessage', 'plan') and interrupted is None:
                    interrupted = 'decision_model_attempted_tool'
                    await self.rpc('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
            if e.get('method') == 'thread/tokenUsage/updated' and budget_usd is not None and interrupted is None:
                spend = account_codex(observations, self.model, turn_id)['cost_usd']
                if spend is not None and budget_usd * .75 <= spend <= budget_usd and not budget_notice_sent:
                    budget_notice_sent = True
                    await self.budget_notice(thread_id, turn_id, spend, budget_usd)
                if spend is not None and spend > budget_usd:
                    interrupted = 'budget_boundary_exceeded'
                    try:
                        await self.rpc('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
                    except RuntimeError:
                        # The final usage event can race with natural turn completion.
                        # Keep consuming the actual completed event; all spend counts.
                        pass
            if e.get('method') == 'turn/completed' and e['params']['turn']['id'] == turn_id:
                status = e['params']['turn']['status']
                break
        events = read_events(self.log_dir / 'app-server.events.jsonl')
        result = {'thread_id': thread_id, 'turn_id': turn_id, 'status': status, 'interrupted': interrupted,
                  'usage': account_codex(events, self.model, turn_id), 'evidence': public_evidence(events)}
        (self.log_dir / 'phase-result.json').write_text(json.dumps(result, indent=2) + '\n')
        if no_tools and interrupted:
            raise RuntimeError(interrupted)
        return result
