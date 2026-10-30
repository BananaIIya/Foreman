from v41_runtime.coordinator import Coordinator as PreviousCoordinator
from .policy import INITIAL_WORK


class Coordinator(PreviousCoordinator):
    async def work(self, phase, payload, policy, cap):
        if phase == 'worker-discovery' or phase.startswith('worker-preparation-'):
            policy = INITIAL_WORK
        return await super().work(phase, payload, policy, cap)
