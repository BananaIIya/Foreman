"""V59 (draft from V58): low-cost worker first attempts the full task; the expert reviews and completes it in the same workspace."""
from v28_runtime.coordinator import Coordinator as PreviousCoordinator, Budget
from v9_runtime.coordinator import Coordinator as NativeCoordinator
from v9_runtime.policy import PREPARE, WORKER
from v8_runtime.policy import route
from dataclasses import dataclass, fields
from v9_runtime.policy import Budget as V9Budget
from .policy import INITIAL_WORK


@dataclass
class UnsplitBudget(Budget):
    def advisor_allowance(self):
        return V9Budget.advisor_allowance(self)


class Coordinator(PreviousCoordinator):
    # Whole-trial target 73% of the verified-correct solo cost (acceptance needs >=25% savings per task).
    SAVINGS_TARGET = .27
    INITIAL_WORKER_SHARE = .06
    WORKER_REPAIR_SHARE = .10

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # V59: no forced 70/30 split of the first expert allowance. V58 evidence: the split either
        # interrupted the expert mid-work (HOF) or left funds unused after an early stop (CAD).
        self.budget = UnsplitBudget(**{f.name: getattr(self.budget, f.name) for f in fields(UnsplitBudget)})
        self.budget.savings_target = self.SAVINGS_TARGET

    async def work(self, phase, payload, policy, cap):
        # Bypass the old preparation-only policy override without changing ledger accounting.
        return await NativeCoordinator.work(self, phase, payload, policy, cap)

    def promote_to_advisor(self, decision, reason):
        promoted = dict(decision, action='advisor',
            intervention='Take ownership of completing the original task in the current workspace. Resolve the observed issue, reusing the existing worker attempt where valid, then produce the original required artifacts and perform their substantive checks. The prior worker-only scope and exclusions do not constrain this advisor intervention. Do not stop after repairing or testing only part of it.',
            acceptance='The original required deliverable is generated and meaningfully checked against the original inputs. A worker draft, component or synthetic fixture is not the deliverable.')
        self.emit('advisor-scope-promotion-'+str(self.budget.advisor_calls), {
            'reason':reason, 'original_worker_decision':decision,
            'advisor_decision':promoted, 'verifier_feedback':False,
            'no_new_model_call':True})
        return promoted

    async def consult(self, instruction, decision, evidence, cycle):
        if decision['action'] != 'advisor':
            decision = self.promote_to_advisor(decision, 'controller_promoted_worker_action')
        return await super().consult(instruction, decision, evidence, cycle)

    async def run(self, instruction):
        b = self.budget
        result = await self.work('worker-discovery', {'original_task': instruction,
            'scope': 'First attempt at the full original task by the low-cost worker; the expert reviews it before delivery.'},
            INITIAL_WORK, min(b.ceiling-b.spent, b.baseline*self.INITIAL_WORKER_SHARE))
        b.worker_phase_floor = result['usage']['cost_usd'] or 0.0
        preparation_spent = b.worker_phase_floor
        stage = 'discovery'  # The expert reviews the worker attempt before any delivery.
        terminal = 'maximum_checkpoints_reached'
        for cycle in range(6):
            if not b.can_continue():
                terminal = 'budget_exhausted'
                break
            context = {'stage': stage, 'original_task': instruction, 'worker_evidence': result['evidence'],
                'preparations_used': b.preparations, 'self_checks_used': b.self_checks, 'advisor_calls_used': b.advisor_calls,
                'remaining_cost_target_usd': b.ceiling-b.spent, 'previous_interventions': [r for r in self.records if r['role'] == 'advisor']}
            gate = await self.decision('decision-' + str(cycle), context)
            self.record('decision', 'decision-' + str(cycle), gate)
            decision = gate['decision']
            if decision['action'] == 'deliver' and result.get('execution') and not result['execution']['all_commands_succeeded']:
                # A passing label cannot erase an observed failed or skipped command.
                decision = dict(decision, action='self_check',
                    intervention='Resolve the actual failed or skipped commands, or show explicitly from the original requirements why they are unnecessary. Preserve their errors in the evidence.')
            if b.advisor_calls == 0 and stage == 'discovery' and decision['action'] == 'deliver':
                decision = self.promote_to_advisor(decision, 'component_is_not_original_deliverable')
            routing_decision = decision
            if b.advisor_calls == 0 and decision['action'] in ('prepare', 'self_check'):
                # The initial work/repair share one 20% allowance below. The
                # legacy generic preparation reserve does not govern this unit.
                routing_decision = dict(decision, action='continue')
            action = route(routing_decision, stage, b, self.history, self.worker_model)
            self.emit('decision-' + str(cycle), {'decision': decision, 'action': action, 'advisor_allowance_usd': b.advisor_allowance()})
            if action in ('deliver', 'budget_exhausted', 'repeated_issue'):
                terminal = action
                break
            if b.advisor_calls == 0 and action in ('continue', 'self_check', 'prepare'):
                allowance = min(b.ceiling-b.spent, max(0.0, b.baseline*self.WORKER_REPAIR_SHARE-preparation_spent))
                if b.preparations >= 1 or allowance < .005:
                    action = 'advisor'
                    decision = self.promote_to_advisor(decision, 'bounded_component_repair_allowance_exhausted')
                    self.emit('decision-'+str(cycle), {'decision': decision, 'action': action,
                        'route_reason': 'bounded_preparation_allowance_exhausted',
                        'advisor_allowance_usd': b.advisor_allowance()})
                else:
                    b.preparations += 1
                    result = await self.work('worker-preparation-'+str(cycle), {
                        'original_task': instruction, 'actual_previous_execution': result['evidence'],
                        'repair_request': decision['intervention'],
                        'scope': 'Repair only the named routine defect in your existing attempt using the actual error; specialist interpretation remains with the expert.'},
                        INITIAL_WORK, allowance)
                    preparation_spent += result['usage']['cost_usd'] or 0.0
                    stage = 'discovery'
                    continue
            if action == 'prepare':
                b.preparations += 1
                result = await self.work('worker-preparation-' + str(cycle), {'original_task': instruction, 'preparation_contract': decision}, PREPARE, b.preparation_allowance(self.worker_model))
                stage = 'preparation'
                continue
            if action == 'advisor':
                result, stage = await self.consult(instruction, decision, result['evidence'], cycle)
                if not b.can_continue():
                    terminal = 'budget_exhausted_after_integration'
                    break
                continue
            if action == 'self_check':
                b.self_checks += 1
            if b.advisor_calls:
                self.integrations += 1
            result = await self.work('worker-implementation-' + str(cycle), {'original_task': instruction, 'checkpoint': decision,
                'execution_evidence': result['evidence']}, WORKER, max(0, b.ceiling-b.spent))
            stage = 'delivery'
        return {'terminal_reason': terminal, 'cost_usd': b.spent if b.usage_complete else None,
            'known_cost_lower_bound_usd': b.spent, 'usage_complete': b.usage_complete,
            'baseline_cost_usd': b.baseline, 'target_ceiling_usd': b.ceiling, 'advisor_calls': b.advisor_calls,
            'preparations': b.preparations, 'integrations': self.integrations,
            'execution_only_integrations': self.executions, 'diagnostic_returns': self.diagnostic_returns,
            'diagnostic_failure_observed': b.diagnostic_failure_observed,
            'advisor_call_limit': b.advisor_call_limit,
            'self_checks': b.self_checks, 'phases': self.records,
            'verifier_feedback': False, 'advisor_workspace_access': True,
            'advisor_code_applied_by_controller': False, 'execution_only_cost_basis': 'No model calls; infrastructure excluded.'}
