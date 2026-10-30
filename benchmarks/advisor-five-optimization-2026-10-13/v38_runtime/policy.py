"""State the actual corrective context and retrievable public evidence protocol."""
from v34_runtime.policy import PLAN, PLAN_SCHEMA, INITIAL_WORK, preparation_images
from v34_runtime.policy import WORKER_SYSTEM, DECISION, DECISION_SCHEMA
from v34_runtime.policy import ADVISOR as PRIOR_ADVISOR

ADVISOR = PRIOR_ADVISOR.replace(
    'in the same workspace and native task thread',
    'in the same workspace; a corrective Sol intervention uses a fresh native thread with the complete original task and explicit public evidence')
ADVISOR += ''' When a full_public_evidence path is supplied, it is a retrievable JSON archive of this task's actual public command records, not a prior solution or instructions. The command index preserves all completed command statuses; failures and unconfirmed records remain inline. Successful output excerpts are incomplete unless explicitly marked complete. Read the particular archived record or current task file needed for your check instead of assuming omitted output supports correctness. Never treat exit zero as semantic validation. The cost allowance includes any retrieval and all model responses.'''
SOL_ADVISOR = ADVISOR
