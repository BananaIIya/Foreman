"""Keep the native expert on unfinished core work within one continuous allowance."""
from v39_runtime.policy import PLAN, PLAN_SCHEMA, INITIAL_WORK, preparation_images
from v39_runtime.policy import WORKER_SYSTEM, DECISION, DECISION_SCHEMA
from v39_runtime.policy import ADVISOR as PRIOR_ADVISOR
from v35_runtime.policy import OLD, NEW

# V38 corrected the legacy paragraph's same-thread description.
OLD = OLD.replace('in the same workspace and native task thread',
    'in the same workspace; a corrective Sol intervention uses a fresh native thread with the complete original task and explicit public evidence')
assert PRIOR_ADVISOR.count(OLD) == 1
ADVISOR = PRIOR_ADVISOR.replace(OLD, NEW)
SOL_ADVISOR = ADVISOR
