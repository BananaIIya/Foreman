"""Bounded specialist ownership, with native evidence-gathering and execution."""
from dataclasses import dataclass
from v8_runtime.policy import Budget as PreviousBudget, DISCOVER, PREPARE, WORKER, WORKER_SYSTEM, DECISION_SCHEMA, evidence_fingerprint
from routing import DECISION as BASE_DECISION

@dataclass
class Budget(PreviousBudget):
    def advisor_allowance(self):
        if not self.usage_complete or self.advisor_calls >= 2:
            return 0.0
        return max(0.0, self.ceiling-self.spent-min(.005,self.baseline*.03))

ADVISOR = '''You own the bounded specialist intervention requested by the executing worker. You have access ONLY to the current task's remote /app workspace. Read actual original inputs, investigate the named uncertainty, directly implement the smallest complete critical correction, and run decisive public requirement checks. Preserve useful existing work. Do not return a large diagnostic framework for a weaker model to interpret: inspect compact observations incrementally and complete the specialist implementation yourself. When an existing library or simple computation suffices, use it. Do not overengineer generic machinery unrelated to the actual task.
The cost target covers all your native responses, including reasoning, cached input, failures and corrections. The controller checks it at the provider's available usage boundaries; a response or short SDK turn may overshoot and remains charged. Prefer concise batched commands and original-input checks. No hidden tests, reference solutions, historical artifacts, benchmark-solution search, other model calls or delegation. No human approval stage. Do not claim unexecuted work. Your tools report real outputs and exit codes. If blocked, state the precise unresolved requirement.
End with compact JSON containing status (completed or needs_advisor, unless your native adapter explicitly requests a short-turn checkpoint vocabulary), summary, relevant_paths, checks (actual command/outcome and original requirement checked), critical_question, unresolved_assumptions. Code belongs in the remote files you edit, not in the final JSON. If you complete and check the required artifact, no weaker-model reconstruction is needed.'''

DECISION = BASE_DECISION + ''' The advisor now has native access to the same remote task workspace and can gather missing observations itself. Do not require a complete diagnostic data dump before escalation. Original images are attached to this request and available to the expert. Avoid preprocessing that attempts to solve the very uncertainty requiring the expert. Native expert execution evidence includes actual commands and exit codes; a completed native turn alone is not proof of task correctness. Inspect the evidence and supplied artifact excerpts against the original requirements. If the expert already completed and substantively checked the artifact, select deliver without another worker implementation. Never demand the weaker worker re-create the specialist implementation.'''
