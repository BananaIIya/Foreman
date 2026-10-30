from v44_runtime.policy import PLAN, PLAN_SCHEMA, WORKER_SYSTEM, DECISION, DECISION_SCHEMA, preparation_images, ADVISOR, SOL_ADVISOR
from v44_runtime.policy import INITIAL_WORK as PREVIOUS_WORK

INITIAL_WORK = PREVIOUS_WORK + '''
The controller prepares /app/component-selfcheck before this phase. Cursor command batches run under Bash -e -o pipefail: ordinary unhandled failures stop that command, and the controller does not execute later commands after a failure. Use explicit conditional handling only for an expected nonfatal condition; preserve its error and explain it. Do not append a successful command just to hide a failed operation. A zero shell exit status still does not prove the component's semantics. The existing command limit, model-call limit and cost allowance remain unchanged.
'''
