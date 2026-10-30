"""Carry available expert progress without additional inference or success claims."""
from v33_runtime.coordinator import Coordinator as PreviousCoordinator, Budget


class Coordinator(PreviousCoordinator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.expert_progress = None
        original_advisor, original_decision = self.advisor, self.decision

        async def advisor(phase, payload):
            if self.expert_progress is not None:
                payload = {**payload, 'expert_progress': self.expert_progress}
            result = await original_advisor(phase, payload)
            self.expert_progress = result.get('expert_progress')
            return result

        async def decision(phase, payload):
            if self.expert_progress is not None:
                payload = {**payload, 'expert_progress': self.expert_progress}
            return await original_decision(phase, payload)

        self.advisor, self.decision = advisor, decision
