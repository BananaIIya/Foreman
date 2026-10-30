"""Expert edits the shared remote workspace; no compulsory worker reconstruction."""
from v8_runtime.coordinator import Coordinator as PreviousCoordinator
from .policy import Budget, evidence_fingerprint

class Coordinator(PreviousCoordinator):
    def __init__(self, baseline, worker_model, worker, decision, advisor, emit):
        async def unused(*args):
            raise AssertionError('Native intervention does not apply JSON replacement files')
        super().__init__(baseline,worker_model,worker,decision,advisor,unused,unused,emit)
        self.budget=Budget(baseline)

    async def consult(self,instruction,decision,evidence,cycle):
        contract=dict(decision,action='advisor')
        if decision['action']!='advisor':
            contract['intervention']='Resolve the reported concrete issue directly in the remote workspace: '+decision['intervention']
        self.history.append(evidence_fingerprint(decision))
        phase='advisor-'+str(self.budget.advisor_calls)
        result=await self.advisor(phase,{'original_task':instruction,'contract':contract,
            'worker_evidence':evidence,'cost_target_usd':self.budget.advisor_allowance()})
        self.record('advisor',phase,result)
        self.budget.advisor_calls+=1
        return result,'delivery'

    async def run(self,instruction):
        result=await super().run(instruction)
        result.update(advisor_workspace_access=True,advisor_code_applied_by_controller=False,
            specialist_integration='Native remote edits and checks; gate may deliver without a worker rewrite.',
            advisor_call_limit=2)
        return result
