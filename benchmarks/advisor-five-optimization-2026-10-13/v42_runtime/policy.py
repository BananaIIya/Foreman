"""Expose verification coverage without treating a self-report as a verdict."""
import copy
from v41_runtime.policy import PLAN, PLAN_SCHEMA, INITIAL_WORK, preparation_images, WORKER_SYSTEM
from v41_runtime.policy import ADVISOR as PREVIOUS_ADVISOR, DECISION as PREVIOUS_DECISION
from v41_runtime.policy import DECISION_SCHEMA as PREVIOUS_SCHEMA

SOURCE_CHECKS = '''
Before relying on an uncertain extraction or transformation, identify how a plausible but wrong result could pass your own implementation's checks. Use the original inputs or independently observable required behavior to discriminate that failure, within the existing allowance. Checking values against the same values your generator assumed, or testing only format and internal consistency, cannot validate the underlying inference. Cover the original substantive requirements, not just the easiest measurable ones.
For non-text inputs, choose an observation that preserves the information needed for the decision. If a numerical summary hides the relevant ambiguity, inspect the original content with an available native modality tool or inspect a faithful visualization generated from the original data. When useful, compare the produced artifact with the source in that representation. A visualization is evidence to examine, not a substitute output; do not make decorative plots or claim to have inspected a file you only generated. Use selective diagnostic views and keep all original data available. Do not import a historical solution or invent reference values.
Perform this check early enough to repair a contradicted assumption and regenerate the required artifact. Explain the actual discriminating observation and what remains unverified in your existing progress/check report; no extra report or model call is required. Successful diagnostics support only the requirements they actually cover. Established domain reasoning can support a requirement when its assumptions are grounded. Do not replace a missing substantive check with a completion label, nor demand unavailable perfect certainty before implementing supported work.
'''
ADVISOR = PREVIOUS_ADVISOR + SOURCE_CHECKS
SOL_ADVISOR = ADVISOR

DECISION_SCHEMA = copy.deepcopy(PREVIOUS_SCHEMA)
ENTRY = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'requirement': {'type': 'string', 'minLength': 1, 'maxLength': 500},
        'basis': {'type': 'string', 'enum': ['original_input', 'required_behavior',
                  'grounded_domain_reasoning', 'internal_consistency_only', 'missing']},
        'observation': {'type': 'string', 'minLength': 1, 'maxLength': 900},
        'material_gap': {'type': 'boolean'},
    },
    'required': ['requirement', 'basis', 'observation', 'material_gap'],
}
DECISION_SCHEMA['properties']['completion_evidence'] = {
    'anyOf': [{'type': 'null'}, {'type': 'array', 'minItems': 1, 'items': ENTRY}]
}
DECISION_SCHEMA['required'].append('completion_evidence')
DECISION = PREVIOUS_DECISION + '''
At delivery after an expert intervention, provide completion_evidence: a compact list covering the original substantive requirements, grouping closely related requirements where appropriate. Each entry identifies the requirement, actual observation or grounded reasoning, its basis, and whether a material gap remains. Cite observed execution or original-input evidence in the observation; a report that a check passed without its meaning is not enough. Distinguish original_input and required_behavior checks from grounded_domain_reasoning and internal_consistency_only. Self-consistency may prove a structural requirement while leaving the core inference unverified: represent those separately. A model's completed status, low residual, or confidence number alone establishes no acceptance threshold unless justified by the original requirement or independent evidence. Do not fabricate a failed check, a numerical threshold, or a need for exhaustive proof. Record missing evidence as missing, not as a failure of the artifact.
Use null before expert work. After expert work, preserve actual failures and missing requirements even if routing cannot fund another intervention. Deliver only when the list covers the original requested outcome and contains no material gap. This is a routing assessment of evidence coverage, not a correctness verdict. It never grants access to a grader or reference solution and never increases the budget. Existing focused corrections and original scope remain in force.
'''
