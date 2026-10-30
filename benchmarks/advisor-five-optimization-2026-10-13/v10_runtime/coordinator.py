"""Useful bounded worker draft followed by focused native expert repair."""
from v9_runtime.coordinator import Coordinator as NativeCoordinator
from v9_runtime.policy import PREPARE, WORKER
from v8_runtime.policy import route
from hybrid_agent import parse_json
from .policy import INITIAL_WORK

class Coordinator(NativeCoordinator):
    async def run(self, instruction):
        b = self.budget
        result = await self.work('worker-discovery', {'original_task': instruction}, INITIAL_WORK, min(b.ceiling,b.baseline*.20))
        b.worker_phase_floor = result['usage']['cost_usd'] or 0.0
        stage = 'delivery' if parse_json(result['evidence'].get('text','')).get('status')=='completed' else 'discovery'
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
            action = route(decision, stage, b, self.history, self.worker_model)
            self.emit('decision-' + str(cycle), {'decision': decision, 'action': action, 'advisor_allowance_usd': b.advisor_allowance()})
            if action in ('deliver', 'budget_exhausted', 'repeated_issue'):
                terminal = action
                break
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
