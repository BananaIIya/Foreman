"""One existing cost warning emphasizes applying supported corrections now."""
import json
from v32_runtime.native import AdvisorSession as PreviousSession


class AdvisorSession(PreviousSession):
    async def budget_notice(self, thread_id, turn_id, spend, cap):
        message = (f'Execution controller cost checkpoint: this intervention has used ${spend:.6f} '
                   f'of ${cap:.6f}; approximately ${max(0, cap-spend):.6f} remains. '
                   'The hard cost boundary is unchanged. Before starting another unresolved investigation, '
                   'apply any correction already supported by current-task evidence to the actual implementation '
                   'or source data, regenerate the required artifact, and run its relevant existing checks. '
                   'Do not leave known supported repairs only in progress notes. If a repair depends on unresolved '
                   'evidence, preserve that uncertainty and do not guess. Keep the exact repeatable delivery manifest '
                   'accurate so the controller can execute the implementation already written. '
                   'Partial repairs do not establish full-task success. Preserve all actual errors and unmet requirements. '
                   'This is measured usage feedback, not grader feedback.')
        receipt = {'spent_usd': spend, 'cap_usd': cap, 'threshold': .75,
                   'thread_id': thread_id, 'turn_id': turn_id, 'message': message,
                   'attempts': 1, 'hard_stop_unchanged': True,
                   'repair_order': 'apply_supported_repairs_before_further_investigation'}
        try:
            result = await self.rpc('turn/steer', {'threadId': thread_id, 'expectedTurnId': turn_id,
                'input': [{'type': 'text', 'text': message, 'text_elements': []}]})
            receipt.update(status='accepted', response=result)
        except (RuntimeError, TimeoutError) as exc:
            receipt.update(status='not_confirmed', error_type=type(exc).__name__)
        (self.log_dir/'budget-notice.json').write_text(json.dumps(receipt, indent=2)+'\n')
