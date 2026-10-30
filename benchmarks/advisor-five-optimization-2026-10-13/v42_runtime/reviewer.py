"""Keep protocol-failure recovery explicit under the evidence-coverage schema."""
from v33_runtime.reviewer import ReviewerSession, reviewed_decision as previous_review


async def reviewed_decision(invoke, phase_dir):
    result = await previous_review(invoke, phase_dir)
    if result.get('controller_recovery'):
        result = {**result, 'decision': {**result['decision'], 'completion_evidence': None}}
    return result
