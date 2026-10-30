"""Reserve a funded correction opportunity before the first expert drains the budget."""
from dataclasses import dataclass, fields
from v20_runtime.coordinator import Coordinator as PreviousCoordinator
from v9_runtime.policy import Budget as PreviousBudget


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
            return await original_advisor(phase, {**payload, 'review_checkpoint': checkpoint})

        self.advisor = checkpoint_advisor
