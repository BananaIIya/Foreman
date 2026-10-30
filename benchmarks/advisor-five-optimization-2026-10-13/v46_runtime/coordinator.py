"""Initial discovery -> specialist core -> grounded late integration -> review."""
from v9_runtime.coordinator import Coordinator as NativeCoordinator
from v32_runtime.completion import unfinished_reason
from .policy import INITIAL_WORK, INTEGRATE, CHECK


class Coordinator(NativeCoordinator):
    def __init__(self, baseline, worker_model, worker, decision, advisor, emit, finalizer, revalidate):
        super().__init__(baseline, worker_model, worker, decision, advisor, emit)
        self.budget.savings_target = .28
        self.finalizer = finalizer
        self.revalidate = revalidate
        self.last_expert = None
        self.progress = None
        self.worker_reserve = baseline * .08

    async def consult(self, instruction, decision, evidence, cycle):
        b = self.budget
        phase = 'advisor-' + str(b.advisor_calls)
        available = b.advisor_allowance()
        reserve = min(self.worker_reserve, available * .20) if b.advisor_calls == 0 else 0.
        payload = {'original_task': instruction, 'contract': dict(decision, action='advisor'),
                   'worker_evidence': evidence, 'cost_target_usd': max(0., available-reserve),
                   'expert_progress': self.progress, 'review_checkpoint': {
                       'phase_allowance_usd': max(0., available-reserve),
                       'late_worker_reserve_usd': reserve,
                       'whole_trial_remaining_usd': max(0., b.ceiling-b.spent),
                       'automatic_extension': False, 'native_overshoot_charged': True}}
        self.emit('checkpoint-budget-' + phase, payload['review_checkpoint'])
        advice = await self.advisor(phase, payload)
        self.record('advisor', phase, advice)
        b.advisor_calls += 1
        self.last_expert = advice
        self.progress = advice.get('expert_progress')
        # Exact implemented delivery commands still run without model inference.
        execution_phase = 'executor-' + str(self.executions)
        delivered = await self.finalizer(execution_phase, advice)
        if delivered is not None:
            assert delivered.get('no_model_call') is True and delivered['usage']['cost_usd'] == 0
            self.record('executor', execution_phase, delivered)
            self.executions += 1
            delivered['late_handoff'] = advice.get('late_handoff')
            return delivered, 'delivery'
        return advice, 'delivery'

    async def run(self, instruction):
        b = self.budget
        result = await self.work('worker-discovery', {'original_task': instruction}, INITIAL_WORK,
                                 min(b.ceiling, b.baseline*.04))
        stage = 'discovery'
        terminal = 'maximum_checkpoints_reached'
        worker_integrated_after_expert = False
        for cycle in range(7):
            if not b.can_continue():
                terminal = 'budget_exhausted'; break
            handoff = result.get('late_handoff')
            payload = {'stage': stage, 'original_task': instruction, 'worker_evidence': result['evidence'],
                       'expert_progress': self.progress, 'advisor_calls_used': b.advisor_calls,
                       'self_checks_used': b.self_checks, 'remaining_cost_target_usd': b.ceiling-b.spent,
                       'verified_late_handoff': handoff if handoff and handoff['status']=='available' else None,
                       'late_handoff_receipt': handoff,
                       'prior_expert_evidence': self.last_expert['evidence'] if self.last_expert and result is not self.last_expert else None}
            gate = await self.decision('decision-' + str(cycle), payload)
            self.record('decision', 'decision-' + str(cycle), gate)
            decision = gate['decision']
            action = decision['action']
            if not b.can_continue():
                terminal = 'budget_exhausted'; break
            commands = result.get('evidence', {}).get('commands', [])
            execution = result.get('execution')
            failed = any(c.get('exit_code', 0) != 0 for c in commands) or bool(execution and not execution['all_commands_succeeded'])
            unfinished = unfinished_reason(self.last_expert) if self.last_expert and not worker_integrated_after_expert else None
            if action == 'deliver' and (stage != 'delivery' or failed or unfinished):
                action = 'advisor'
            if action in ('continue', 'prepare'):
                if not handoff or handoff['status'] != 'available' or self.integrations >= 1:
                    action = 'advisor'
                else:
                    fresh = await self.revalidate(handoff)
                    if fresh['status'] != 'available' or fresh.get('manifest_sha256') != handoff.get('manifest_sha256'):
                        result['late_handoff'] = fresh
                        result['evidence'] = dict(result['evidence'], handoff_revalidation=fresh)
                        action = 'advisor'
                    else:
                        handoff = fresh
            if action == 'self_check' and (stage == 'discovery' or b.self_checks >= 1):
                action = 'advisor'
            self.emit('decision-' + str(cycle), {'decision': decision, 'action': action,
                       'failed_execution': failed, 'unfinished_expert': unfinished,
                       'advisor_allowance_usd': b.advisor_allowance()})
            if action == 'deliver':
                terminal = 'deliver'; break
            if action == 'advisor':
                if b.advisor_calls >= 2 or b.advisor_allowance() <= 0:
                    terminal = 'advisor_limit_or_budget'; break
                if decision['action'] != 'advisor':
                    decision = dict(decision, action='advisor', intervention=
                        'The requested worker action or delivery lacks a valid completed handoff or has unresolved execution evidence. Resolve the actual original requirements in the existing workspace, then finish directly or provide a complete grounded routine handoff. Prior proposed step: '+decision['intervention'])
                result, stage = await self.consult(instruction, decision, result['evidence'], cycle)
                worker_integrated_after_expert = False
                continue
            reserve = min(.005, b.baseline*.03)
            available = max(0., b.ceiling-b.spent-reserve)
            if available <= 0:
                terminal = 'budget_exhausted'; break
            if action == 'self_check':
                b.self_checks += 1
                policy = CHECK
                work = {'original_task': instruction, 'checkpoint': decision, 'execution_evidence': result['evidence']}
                cap = min(available, b.baseline*.04)
                phase = 'worker-check-' + str(cycle)
            else:
                self.integrations += 1
                policy = INTEGRATE
                work = {'original_task': instruction, 'verified_late_handoff': handoff['manifest'],
                        'checkpoint': decision, 'execution_evidence': result['evidence']}
                cap = min(available, self.worker_reserve)
                phase = 'worker-integration-' + str(cycle)
            result = await self.work(phase, work, policy, cap)
            # Only an actual integration can supersede the expert's unfinished report;
            # a check alone cannot manufacture a missing original artifact.
            worker_integrated_after_expert = worker_integrated_after_expert or action != 'self_check'
            stage = 'delivery'
        return {'terminal_reason': terminal, 'cost_usd': b.spent if b.usage_complete else None,
                'known_cost_lower_bound_usd': b.spent, 'usage_complete': b.usage_complete,
                'baseline_cost_usd': b.baseline, 'target_ceiling_usd': b.ceiling,
                'advisor_calls': b.advisor_calls, 'advisor_call_limit': 2,
                'preparations': b.preparations, 'integrations': self.integrations,
                'execution_only_integrations': self.executions, 'diagnostic_returns': 0,
                'diagnostic_failure_observed': False, 'self_checks': b.self_checks,
                'phases': self.records, 'verifier_feedback': False, 'advisor_workspace_access': True,
                'advisor_code_applied_by_controller': False,
                'execution_only_cost_basis': 'No model calls; infrastructure excluded.'}
