"""One evidence-only critique inside the current specialist intervention."""
import json
import math

SCHEMA = {'type':'object','additionalProperties':False,
 'required':['action','requirement','observed_evidence','proposed_check','reason'],
 'properties':{'action':{'type':'string','enum':['continue','check']},
 **{key:{'type':'string','maxLength':1000} for key in ['requirement','observed_evidence','proposed_check','reason']}}}
POLICY = '''You are an inexpensive evidence-only reviewer DURING an ongoing specialist intervention. Use only the complete original task, original images and supplied public command/output evidence. The expert is still working; absence of the final artifact or an unperformed check at this intermediate point is not by itself an error. Identify at most ONE consequential concrete contradiction or unsupported inference that should be checked before the expert builds further work on it. Cite an actual observation or exact current implementation behavior in observed_evidence. The proposed_check should discriminate an original-requirement uncertainty using original inputs; do not invent an expected answer, algorithm parameter, hidden test, historical solution, or a new certification requirement. If no grounded concern exists, action continue with empty concern fields is appropriate. Do not manufacture a concern to justify this review. No tools, execution, delegation or other model calls. Return the required concise JSON. This is advice to the same active expert, not a correctness verdict or a replacement assignment.'''


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
