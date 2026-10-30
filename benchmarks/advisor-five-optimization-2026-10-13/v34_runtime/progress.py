"""Read bounded, phase-bound expert progress as untrusted routing evidence."""
import hashlib
import json
import jsonschema
from v33_runtime.policy import DECISION_SCHEMA

SCHEMA = {'type': 'object', 'additionalProperties': False,
          'properties': {
              'token': {'type': 'string'}, 'phase': {'type': 'string'},
              'status': {'enum': ['in_progress', 'completed', 'blocked']},
              'required_work': {'type': 'string', 'maxLength': 1200},
              'observed_evidence': {'type': 'string', 'maxLength': 1200},
              'priority_check': DECISION_SCHEMA['properties']['priority_check']},
          'required': ['token', 'phase', 'status', 'required_work', 'observed_evidence', 'priority_check']}


def validate_progress(raw, token, phase):
    if len(raw) > 12288:
        raise ValueError('Progress record exceeds 12 KiB')
    value = json.loads(raw)
    jsonschema.validate(value, SCHEMA)
    if value['token'] != token or value['phase'] != phase:
        raise ValueError('Progress record belongs to a different intervention')
    if value['status'] != 'completed' and not value['required_work'].strip():
        raise ValueError('Unfinished progress lacks its original required work')
    return value


async def collect_progress(contract, destination, download):
    receipt = {'phase': contract['phase'], 'path': contract['path'],
               'model_calls': 0, 'grader_calls': 0,
               'correctness_proven': False, 'report': None}
    try:
        await download(contract['path'], destination)
        if destination.stat().st_size > 12288:
            raise ValueError('Progress record exceeds 12 KiB')
        raw = destination.read_bytes()
        report = validate_progress(raw, contract['token'], contract['phase'])
        receipt.update(status='available', sha256=hashlib.sha256(raw).hexdigest(), report=report)
    except (OSError, ValueError, jsonschema.ValidationError) as exc:
        receipt.update(status='unavailable_or_invalid', error_type=type(exc).__name__)
    except Exception as exc:
        # A diagnostic download failure cannot fabricate task failure or success.
        receipt.update(status='download_failed', error_type=type(exc).__name__)
    return receipt
