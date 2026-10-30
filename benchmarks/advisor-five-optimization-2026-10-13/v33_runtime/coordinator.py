"""Forward a grounded correction contract without another paid planning call."""
from v32_runtime.coordinator import Coordinator as PreviousCoordinator, Budget


class Coordinator(PreviousCoordinator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        original = self.advisor

        async def focused_advisor(phase, payload):
            focus = payload.get('contract', {}).get('priority_check')
            corrective = self.budget.advisor_calls > 0
            if corrective and focus:
                # Keep the original task and decision untouched. The structured
                # check is a priority, not a replacement assignment or verdict.
                payload = {**payload, 'corrective_focus': dict(focus)}
                status = 'grounded_priority_forwarded'
            else:
                status = 'initial_intervention' if not corrective else 'no_grounded_priority_available'
            self.emit('corrective-focus-' + phase, {
                'status': status, 'priority_check': focus if corrective else None,
                'original_task_preserved': True, 'extra_model_calls': 0,
                'verifier_feedback': False, 'success_claimed': False})
            return await original(phase, payload)

        self.advisor = focused_advisor
