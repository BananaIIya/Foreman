"""Bounded expert diagnostic loop and execution-only integration."""
from .policy import Budget, DISCOVER, PREPARE, WORKER, route, evidence_fingerprint, validate_patch
from v4_runtime.coordinator import Coordinator as BaseCoordinator

class Coordinator(BaseCoordinator):
    def __init__(self, baseline, worker_model, worker, decision, advisor, apply_patch, execute, emit):
        super().__init__(baseline, worker_model, worker, decision, advisor, apply_patch, emit)
        self.execute = execute
        self.executions = 0
        self.diagnostic_returns = 0

    def record(self, role, phase, result):
        if result['usage']['cost_usd'] is None:
            self.budget.spent += result['usage'].get('known_cost_lower_bound_usd', 0.0)
        super().record(role, phase, result)

    async def consult(self, instruction, decision, evidence, cycle):
        b = self.budget
        contract = dict(decision, action='advisor')
        if decision['action'] != 'advisor':
            contract.update(deferred_worker_action=decision['action'], deferred_worker_step=decision['intervention'],
                intervention='Resolve the critical issue directly using the original inputs and actual evidence. Supply complete executable files and setup/validation commands; request diagnostics only if essential.',
                acceptance='The controller can execute the correction and observe checks against the original requirements. Do not claim unexecuted success.')
        # Routing identifies a question; it must not shrink the expert's executable
        # implementation responsibility to a prose answer (observed in v5 CAD).
        contract.update(specialist_question=decision['intervention'],
            original_acceptance=decision['acceptance'],
            intervention='Resolve the specialist question AND implement that resolution in complete executable replacement files. The original task defines the required final artifact. For generated-artifact tasks, provide the complete runnable generator that writes the required artifact, not dimensions, formulas or an algorithm fragment for the weaker worker to reconstruct. Preserve existing correct code when supplied. Include dependency setup, program invocation, and semantic validation commands. If essential evidence is missing, provide diagnostic files/commands with needs_context. A prose-only answer labeled patch is invalid.',
            acceptance='Running the supplied files and commands produces the final required artifact and checks its substantive original requirements. State assumptions honestly. The executing worker should need routine execution only, not another attempt at the specialist reasoning or implementation.')
        self.history.append(evidence_fingerprint(decision))
        while True:
            phase = 'advisor-' + str(b.advisor_calls)
            advice = await self.advisor(phase, {'original_task': instruction, 'contract': contract,
                'worker_evidence': evidence, 'cost_target_usd': b.advisor_allowance(),
                'diagnostic_returns_used': self.diagnostic_returns})
            self.record('advisor', phase, advice)
            b.advisor_calls += 1
            patch = advice.get('patch')
            receipt = {'applied': False}
            # Validate only model protocol here. An installation/transport error is infrastructure.
            try:
                validate_patch(patch)
            except (ValueError, TypeError, KeyError) as exc:
                receipt['protocol_error'] = str(exc)
            except Exception as exc:
                import jsonschema
                if not isinstance(exc, jsonschema.ValidationError):
                    raise
                receipt['protocol_error'] = str(exc).split('\n')[0]
            if 'protocol_error' in receipt and b.advisor_allowance() > 0:
                # Correct the actual malformed handoff with the same expert role.
                # This consumes the same two-call budget, never a hidden free retry.
                evidence = {'text': 'Your previous handoff failed the executable protocol: '+receipt['protocol_error']+'\nPrevious public answer:\n'+advice['evidence']['text']}
                contract = dict(contract, intervention=contract['intervention']+' Return a valid complete handoff now; include nonempty full file contents and commands for a patch.')
                continue
            if 'protocol_error' not in receipt and patch['status'] in ('patch', 'needs_context'):
                receipt = await self.apply_patch(patch)
                if receipt.get('applied'):
                    execution_phase = 'executor-' + str(self.executions)
                    result = await self.execute(execution_phase, patch)
                    assert result.get('no_model_call') is True and result['usage']['cost_usd'] == 0
                    self.record('executor', execution_phase, result)
                    self.executions += 1
                    result['execution']['patch_receipt'] = receipt
                    if patch['status'] == 'patch':
                        return result, 'delivery'
                    # Return real diagnostic observations directly to the expert, once.
                    if self.diagnostic_returns < 1 and b.advisor_allowance() > 0:
                        self.diagnostic_returns += 1
                        evidence = result['evidence']
                        contract = dict(contract, evidence=evidence['text'], paths=receipt.get('paths', []),
                            intervention='Use the actual diagnostic results to complete the original intervention. Correct any failed diagnostic assumptions using the original task. Supply the final executable correction; only one diagnostic return is available.')
                        continue
                    return result, 'diagnostic'
            # Invalid/incomplete handoff gets one real worker integration even after overspend.
            handoff = {'original_task': instruction, 'specialist_status': advice['status'], 'patch_receipt': receipt,
                'specialist_handoff': {k: v for k, v in patch.items() if k != 'files'} if isinstance(patch, dict) else {},
                'raw_advice_if_unapplied': advice['evidence']['text'], 'last_integration_opportunity': not b.can_continue()}
            result = await self.work('worker-integration-' + str(cycle), handoff, WORKER, b.integration_cap(self.worker_model))
            self.integrations += 1
            return result, 'delivery'

    async def run(self, instruction):
        b = self.budget
        result = await self.work('worker-discovery', {'original_task': instruction}, DISCOVER, b.ceiling)
        b.worker_phase_floor = result['usage']['cost_usd'] or 0.0
        stage = 'discovery'
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
            result = await self.work('worker-implementation-' + str(cycle), {'original_task': instruction, 'checkpoint': decision,
                'execution_evidence': result['evidence']}, WORKER, max(0, b.ceiling-b.spent))
            stage = 'delivery'
        return {'terminal_reason': terminal, 'cost_usd': b.spent if b.usage_complete else None,
            'known_cost_lower_bound_usd': b.spent, 'usage_complete': b.usage_complete,
            'baseline_cost_usd': b.baseline, 'target_ceiling_usd': b.ceiling, 'advisor_calls': b.advisor_calls,
            'preparations': b.preparations, 'integrations': self.integrations,
            'execution_only_integrations': self.executions, 'diagnostic_returns': self.diagnostic_returns,
            'self_checks': b.self_checks, 'phases': self.records,
            'verifier_feedback': False, 'advisor_workspace_access': False,
            'advisor_code_applied_by_controller': True, 'execution_only_cost_basis': 'No model calls; infrastructure excluded.'}
