"""Prevent an admitted validation gap from becoming a delivery verdict."""
from v41_runtime.coordinator import Coordinator as PreviousCoordinator


def unsupported_requirement(decision):
    entries = decision.get('completion_evidence')
    if not entries:
        return 'The delivery review supplied no substantive requirement coverage.'
    for entry in entries:
        if entry['material_gap'] or entry['basis'] in ('internal_consistency_only', 'missing'):
            return entry['requirement'] + ': ' + entry['observation']
    return None


class Coordinator(PreviousCoordinator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        original_decision = self.decision

        async def decision(phase, payload):
            result = await original_decision(phase, payload)
            proposed = result['decision']
            if self.budget.advisor_calls and proposed['action'] == 'deliver':
                gap = unsupported_requirement(proposed)
                if gap:
                    replacement = dict(proposed, action='advisor',
                        issue='The routing review admits a material evidence gap.',
                        evidence=gap,
                        intervention='Use the actual current workspace to resolve the admitted original-requirement evidence gap. Perform the smallest discriminating source or behavior check, repair only a supported error, and regenerate the original deliverable if needed. Do not repeat checks that share the same unsupported assumption. Preserve all original requirements and honest uncertainty.',
                        acceptance='The original requirement has substantive support, with the actually executed check or grounded reasoning and any remaining gap recorded.')
                    self.emit('evidence-coverage-review-'+phase, {
                        'original_decision': proposed, 'replacement_decision': replacement,
                        'gap': gap, 'correctness_claimed': False, 'verifier_feedback': False,
                        'extra_model_calls': 0, 'budget_reset': False})
                    return {**result, 'decision': replacement}
            return result

        self.decision = decision
