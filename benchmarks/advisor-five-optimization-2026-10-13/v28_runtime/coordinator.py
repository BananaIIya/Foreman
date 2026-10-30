"""Reserve a funded correction opportunity before the first expert drains the budget."""
from dataclasses import dataclass, fields
from v20_runtime.coordinator import Coordinator as PreviousCoordinator
from v9_runtime.policy import Budget as PreviousBudget
from .completion import unfinished_reason


@dataclass
class Budget(PreviousBudget):
    def advisor_allowance(self):
        available = super().advisor_allowance()
        return available * .70 if self.advisor_calls == 0 else available


class Coordinator(PreviousCoordinator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget = Budget(**{f.name: getattr(self.budget, f.name) for f in fields(Budget)})
        original_advisor = self.advisor
        original_decision = self.decision
        self.last_expert = None

        async def checkpoint_advisor(phase, payload):
            b = self.budget
            total_available = PreviousBudget.advisor_allowance(b)
            first = b.advisor_calls == 0
            checkpoint = {
                'kind': 'first_intervention_review' if first else 'focused_correction',
                'phase_allowance_usd': payload['cost_target_usd'],
                'correction_reserve_usd': max(0., total_available - payload['cost_target_usd']),
                'whole_trial_remaining_usd': max(0., b.ceiling - b.spent),
                'automatic_extension': False,
                'native_overshoot_charged': True,
            }
            self.emit('checkpoint-budget-' + phase, checkpoint)
            result = await original_advisor(phase, {**payload, 'review_checkpoint': checkpoint})
            self.last_expert = (phase, result)
            return result

        async def completion_review(phase, payload):
            result = await original_decision(phase, payload)
            if result['decision']['action']=='deliver' and self.last_expert:
                expert_phase, expert_result = self.last_expert
                reason = unfinished_reason(expert_result)
                if reason:
                    original = result['decision']
                    decision = dict(original, action='advisor',
                        issue='The expert intervention has not established completion of its original assignment.',
                        evidence=reason + '. The proposed routing verdict and actual execution evidence are retained.',
                        intervention='Continue from existing work. Finish the outstanding original requirements and decisive checks identified in your progress report, regenerate the final artifact, and return an honest completion or unresolved-work report. Do not restart supported work.',
                        acceptance='The original deliverable is actually generated and meaningfully checked; a progress checkpoint is not a completion report.')
                    self.emit('completion-review-'+phase,{'reason':reason,'expert_phase':expert_phase,
                              'original_decision':original,'replacement_decision':decision,
                              'no_new_model_call':True,'verifier_feedback':False})
                    return dict(result,decision=decision)
            return result

        self.advisor = checkpoint_advisor
        self.decision = completion_review
