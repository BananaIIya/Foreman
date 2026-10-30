"""Apply supported repairs before further unresolved investigation."""
from v38_runtime.policy import PLAN, PLAN_SCHEMA, INITIAL_WORK, preparation_images
from v38_runtime.policy import WORKER_SYSTEM, DECISION_SCHEMA
from v38_runtime.policy import DECISION as PRIOR_DECISION, ADVISOR as PRIOR_ADVISOR

REPAIR_ORDER = '''
Work in short observe-repair-check cycles. Once current-task evidence supports a concrete correction to an original requirement, apply that correction to the actual implementation or source data and regenerate the original deliverable BEFORE investigating a different unresolved question. Do not accumulate supported repairs only in notes while the delivered artifact remains unchanged. An unresolved requirement does not prevent applying an independent, already supported correction; preserve uncertain values and report uncertainty instead of guessing them.
After a supported repair, run the relevant existing checks and keep the repeatable delivery manifest accurate. Continue investigating other unmet original requirements within the remaining allowance. This ordering does not reduce the required scope or make a partial result correct. If a repair truly depends on unresolved evidence, state that dependency and investigate it; never force an unsupported edit merely to change the artifact. In progress.required_work distinguish supported repairs still unapplied from questions requiring further evidence. In observed_evidence distinguish a diagnostic finding from a repair actually reflected in the current deliverable. At a cost warning, prioritize applying supported outstanding repairs and regenerating/checking the actual output before starting more analysis. A report of a correction is not its execution.
'''
ADVISOR = PRIOR_ADVISOR + REPAIR_ORDER
SOL_ADVISOR = ADVISOR
DECISION = PRIOR_DECISION + ''' If current evidence already supports a concrete correction but the original deliverable has not been updated, prioritize applying that supported correction before requesting another investigation. Keep genuinely unresolved questions visible and preserve full-task completion requirements. Do not infer that a progress note describing a finding means it was implemented.'''
