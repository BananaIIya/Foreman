"""Remove only exact duplicate command records from prompt copies, not raw logs."""
import copy
import json


def compact_evidence(evidence):
    if not isinstance(evidence, dict) or not isinstance(evidence.get('commands'), list):
        return evidence
    try:
        report = json.loads(evidence.get('text', ''))
    except (TypeError, ValueError):
        return evidence
    if not isinstance(report, dict):
        return evidence
    commands = evidence['commands']
    if not commands or report.get('actual_command_results') != commands:
        return evidence
    result = copy.deepcopy(evidence)
    report.pop('actual_command_results')
    report['actual_command_results_reference'] = 'The complete actual command records are in the sibling evidence.commands array.'
    # Preserve history order. Replace a batch only if it is exactly the next
    # contiguous slice of the already retained canonical command records.
    offset = 0
    for entry in report.get('protocol_history', []):
        if not isinstance(entry, dict):
            continue
        batch = entry.get('actual_command_results')
        if isinstance(batch, list) and batch and commands[offset:offset+len(batch)] == batch:
            entry.pop('actual_command_results')
            entry['actual_command_result_indexes'] = list(range(offset, offset+len(batch)))
            offset += len(batch)
    result['text'] = json.dumps(report, ensure_ascii=False)
    result['command_records_deduplicated'] = True
    return result


def compact_payload(payload):
    result = dict(payload)
    for key in ('worker_evidence', 'execution_evidence', 'actual_previous_execution'):
        if key in result:
            result[key] = compact_evidence(result[key])
    return result
