"""Retain explicit tool-violation recovery without inventing a diagnostic focus."""
from v32_runtime.reviewer import ReviewerSession, reviewed_decision as previous_review


async def reviewed_decision(invoke, phase_dir):
    result = await previous_review(invoke, phase_dir)
    if result.get('controller_recovery'):
        result = {**result, 'decision': {**result['decision'], 'priority_check': None}}
    return result
