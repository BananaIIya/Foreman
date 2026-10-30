"""The executor's own transcript, rendered for the advisor (official pattern: advisor sees shared context).

Only public items the executor produced or observed: its messages, commands with exit codes and
output tails, file edits, and earlier advice. No hidden tests, grader output or private reasoning.
"""
import json
from pathlib import Path

COMMAND_CHARS, OUTPUT_CHARS, MESSAGE_CHARS, DIFF_CHARS = 800, 1500, 2500, 1500


def _head(text, n):
    text = text or ''
    return text if len(text) <= n else text[:n] + f'\n…[{len(text) - n} more chars]'


def _tail(text, n):
    text = text or ''
    return text if len(text) <= n else f'[{len(text) - n} earlier chars omitted]…\n' + text[-n:]


def completed_items(events_path):
    items = []
    for line in Path(events_path).read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('method') == 'item/completed':
            items.append(event['params']['item'])
    return items


def render(items):
    out = []
    for item in items:
        kind = item.get('type')
        if kind == 'agentMessage':
            out.append('EXECUTOR: ' + _head(item.get('text'), MESSAGE_CHARS))
        elif kind == 'commandExecution':
            out.append(f"COMMAND (exit {item.get('exitCode')}): {_head(str(item.get('command')), COMMAND_CHARS)}\n"
                       f"OUTPUT: {_tail(item.get('aggregatedOutput'), OUTPUT_CHARS)}")
        elif kind == 'fileChange':
            for change in item.get('changes', []):
                out.append(f"EDIT {change.get('path')}: {_head(str(change.get('diff') or ''), DIFF_CHARS)}")
        elif kind == 'dynamicToolCall':
            content = ' '.join(c.get('text', '') for c in (item.get('contentItems') or []) if isinstance(c, dict))
            out.append(f"ADVISOR CALL {json.dumps(item.get('arguments'))[:400]} -> {_head(content, 1500)}")
    return '\n\n'.join(out)
