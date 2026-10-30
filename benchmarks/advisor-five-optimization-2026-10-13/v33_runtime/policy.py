"""Focus corrective specialist work on one falsifiable uncertainty at a time."""
import copy
from v32_runtime.policy import PLAN, PLAN_SCHEMA, INITIAL_WORK, preparation_images, WORKER_SYSTEM
from v32_runtime.policy import DECISION as PRIOR_DECISION, DECISION_SCHEMA as PRIOR_SCHEMA
from v32_runtime.policy import ADVISOR as PRIOR_ADVISOR

DECISION_SCHEMA = copy.deepcopy(PRIOR_SCHEMA)
DECISION_SCHEMA['properties']['priority_check'] = {
    'anyOf': [
        {'type': 'null'},
        {'type': 'object', 'additionalProperties': False,
         'properties': {k: {'type': 'string', 'minLength': 1, 'maxLength': 900} for k in
                        ['primary_uncertainty', 'alternative_explanations',
                         'discriminating_observation', 'repair_goal']},
         'required': ['primary_uncertainty', 'alternative_explanations',
                      'discriminating_observation', 'repair_goal']}
    ]
}
DECISION_SCHEMA['required'] = [*PRIOR_SCHEMA['required'], 'priority_check']
DECISION = PRIOR_DECISION + '''
For a corrective advisor request AFTER an advisor has already worked, fill priority_check with ONE highest-impact unresolved question supported by actual execution evidence. State the competing explanations and a concrete observation from original inputs or required behavior that can distinguish them. Specify which original requirement should be repaired if the check fails. These are a requested experiment and repair goal, NOT an invented result or answer. Favor unresolved causes affecting many outputs over isolated cosmetic defects. Repeating the implementation's own inferred values is not independent validation. Do not request a wide audit of every possible uncertainty in this intervention. Preserve the full original deliverable and record other unresolved requirements honestly.
Use null for priority_check when no grounded focused correction can be identified, on initial escalation, or for a non-advisor action. Lack of this field's evidence must not be filled with a guess. A reviewer protocol failure is not evidence of a task defect. You are still only routing; do not solve the task or prescribe unsupported specialist parameters.'''

ADVISOR = PRIOR_ADVISOR + '''
When a corrective_focus contract is present, first resolve that one uncertainty using an observation that distinguishes the competing explanations. If its proposed experiment is unsound or unavailable, replace it with the smallest defensible independent check of the same question and state why. Use existing workspace code and measurements. Prioritize a cause that can affect many outputs; do not spend the correction allowance first polishing one low-impact example while the main ambiguity remains.
After the check, implement the supported correction in the original artifact and rerun the existing relevant checks. If the proposed concern is disproved, preserve supported work and move to the next actual unmet original requirement only with remaining budget. Do not stop at collecting disagreement statistics or producing another audit report. Do not change outputs merely to make a check agree. If evidence is insufficient, retain that uncertainty honestly. Include a compact corrective_result in the final JSON: question, observation, conclusion (supported/disproved/unresolved), repair_applied, checks actually executed. This work still owns the full original task; resolving one question is not permission to declare all requirements complete.'''
SOL_ADVISOR = ADVISOR
