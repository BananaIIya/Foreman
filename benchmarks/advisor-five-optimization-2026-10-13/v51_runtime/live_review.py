"""One evidence-only critique inside the current specialist intervention."""
import json
import math

SCHEMA = {'type':'object','additionalProperties':False,
 'required':['action','requirement','observed_evidence','proposed_check','reason'],
 'properties':{'action':{'type':'string','enum':['continue','check']},
 **{key:{'type':'string','maxLength':1000} for key in ['requirement','observed_evidence','proposed_check','reason']}}}
REVIEW_THRESHOLD = .65
SCHEMA['required'].append('validation_basis')
SCHEMA['properties']['validation_basis'] = {'type':'string','enum':[
    'original_input','independent_requirement','derived_values_only','structural_only','not_yet_observed']}
POLICY = """You are an inexpensive evidence-only reviewer inside an ongoing specialist intervention, around 65% of its measured budget. Use only the original task, original images, actual public command/output evidence and the supplied current workspace excerpts. Excerpts may be unavailable or explicitly truncated; never infer omitted content or task completion from metadata. No hidden grader, reference answer, historical solution, tools, execution or other model calls are available.
Assess what the observed validation actually establishes for the most consequential original requirement. validation_basis describes the basis of the relevant observed check: original_input (a substantive check against original observations), independent_requirement (a requirement-derived invariant independent of guessed solution values), derived_values_only (output matches values produced by the same inference or generator), structural_only (parsing, shape or serialization), or not_yet_observed. A generated artifact matching the inferred values used to write it is a valid serialization check but does not independently validate those inferences. A test that would accept competing incorrect interpretations is insufficient evidence for choosing among them. Neither this classification nor an expert completion label is a correctness verdict.
Return action check only for ONE consequential grounded contradiction or unsupported original-requirement claim. Cite the exact observed check or implementation behavior and the original requirement it leaves unsupported. Propose a small discriminating observation/check against original inputs that can falsify that assumption, then a focused repair if warranted. Do not invent expected values, prescribe a benchmark-specific answer, request a broad restart, or demand formal proof, external certification or optional research. Prioritize broadly unsupported required behavior over a local choice already supported by evidence. Established domain knowledge and actual computations can provide adequate support without an additional independent solver.
The expert is still working. Missing final output or a not-yet-run check alone is not an error; do not manufacture a concern merely because execution is unfinished. If available evidence already supports the relevant inference, or no concrete concern is grounded, return continue with empty concern fields and an honest validation_basis. Your advice goes to the SAME expert within the unchanged remaining budget. Return the required concise JSON; this is a hypothesis to triage, not a replacement assignment or a hidden grading result."""


def allowance(cap):
    return min(.005, cap*.02) if cap and cap>0 else 0.


def feedback(report):
    if not isinstance(report, dict) or report.get('action') != 'check':
        return None
    if not all(isinstance(report.get(k),str) and report[k].strip() for k in ['requirement','observed_evidence','proposed_check']):
        return None
    return ('A cheap reviewer inspected the current public execution evidence. This is a hypothesis, not a verdict or grader feedback. '
            'Triage the concrete concern against the actual original inputs, preserve supported work and complete the original deliverable within the SAME remaining budget. '
            'If the concern is unsupported or already resolved, explain briefly and continue; do not restart the task.\n'+json.dumps(report,ensure_ascii=False))


def known_cost(result):
    value=result.get('usage',{}).get('cost_usd')
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None
