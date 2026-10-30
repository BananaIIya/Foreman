"""V58: exact-duplicate removal plus bounded command records in prompt copies.

Raw logs and workspace files keep everything; only the copy sent to the next
model is bounded, because every token there is re-read on each later turn.
"""
import copy
from v20_runtime.evidence import compact_evidence

MAX_COMMANDS = 6
MAX_COMMAND_CHARS = 600
MAX_OUTPUT_CHARS = 1200


def _head(text, limit):
    text = text or ''
    return text if len(text) <= limit else text[:limit] + f'\n...[{len(text)-limit} more characters in the workspace/raw log]'


def _tail(text, limit):
    text = text or ''
    return text if len(text) <= limit else f'[{len(text)-limit} earlier characters omitted; full output in raw log]...\n' + text[-limit:]


def bound_evidence(evidence):
    if not isinstance(evidence, dict) or not isinstance(evidence.get('commands'), list):
        return evidence
    result = copy.deepcopy(evidence)
    commands = result['commands']
    omitted = max(0, len(commands) - MAX_COMMANDS)
    kept = []
    for row in commands[-MAX_COMMANDS:]:
        row = dict(row) if isinstance(row, dict) else {'record': row}
        if isinstance(row.get('command'), str):
            row['command'] = _head(row['command'], MAX_COMMAND_CHARS)
        for key in ('output', 'stdout', 'stderr'):
            if isinstance(row.get(key), str):
                row[key] = _tail(row[key], MAX_OUTPUT_CHARS)
        kept.append(row)
    result['commands'] = kept
    if omitted:
        result['earlier_commands_omitted'] = omitted
    result['prompt_copy_bounded'] = {'max_commands': MAX_COMMANDS, 'max_command_chars': MAX_COMMAND_CHARS,
                                     'max_output_chars': MAX_OUTPUT_CHARS,
                                     'note': 'Exit codes are kept; full commands and outputs remain in the workspace and raw logs.'}
    return result


def compact_payload(payload):
    result = dict(payload)
    for key in ('worker_evidence', 'execution_evidence', 'actual_previous_execution'):
        if key in result:
            result[key] = bound_evidence(compact_evidence(result[key]))
    return result
