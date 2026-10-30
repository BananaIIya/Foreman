"""Finish expert-provided routine delivery even after model budget exhaustion."""
from v15_runtime.coordinator import Coordinator as PlannedCoordinator


class Coordinator(PlannedCoordinator):
    def __init__(self, baseline, worker_model, worker, decision, advisor, emit, planner, finalizer):
        super().__init__(baseline, worker_model, worker, decision, advisor, emit, planner)
        self.finalizer = finalizer

    async def consult(self, instruction, decision, evidence, cycle):
        advice, stage = await super().consult(instruction, decision, evidence, cycle)
        phase = 'executor-' + str(self.executions)
        delivery = await self.finalizer(phase, advice)
        if delivery is None:
            return advice, stage
        assert delivery.get('no_model_call') is True and delivery['usage']['cost_usd'] == 0
        self.record('executor', phase, delivery)
        self.executions += 1
        return delivery, 'delivery'
