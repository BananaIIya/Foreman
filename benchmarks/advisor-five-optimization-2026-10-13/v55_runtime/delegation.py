"""Bounded routine worker calls whose cost belongs to the current expert turn.

This module does not start a model, read task answers, or execute a command.
The coordinator supplies the existing worker implementation and current costs.
"""
import asyncio
import copy
import hashlib
import json
import math
from pathlib import PurePosixPath

import jsonschema


SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['work_kind', 'procedure', 'input_paths', 'output_paths',
                 'given_facts', 'hypothesis', 'decision_rule', 'acceptance_checks'],
    'properties': {
        'procedure': {'type': 'string', 'minLength': 1, 'maxLength': 5000},
        'input_paths': {'type': 'array', 'minItems': 1, 'maxItems': 8,
                        'items': {'type': 'string', 'maxLength': 500}},
        'output_paths': {'type': 'array', 'minItems': 1, 'maxItems': 8,
                         'items': {'type': 'string', 'maxLength': 500}},
        'work_kind': {'type':'string','enum':['implementation','experiment']},
        'given_facts': {'type':'string','minLength':1,'maxLength':2000},
        'hypothesis': {'type':'string','maxLength':2000},
        'decision_rule': {'type':'string','minLength':1,'maxLength':2000},
        'acceptance_checks': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                             'items': {'type': 'string', 'minLength': 1, 'maxLength': 1000}},
    },
}

TOOL = {
    'type': 'function', 'name': 'routine_worker', 'deferLoading': False,
    'description': (
        'Delegate one precisely specified implementation OR experiment to the assigned '
        'worker in the SAME task workspace. The specialist designs the algorithm and '
        'interprets its results; the worker writes and executes the code. Actual core '
        'conclusions do NOT need to be known before an experiment. Distinguish observed '
        'given_facts from the unproven hypothesis; give an executable procedure and a '
        'decision_rule for interpreting its observations. The worker executes at '
        'most two commands in one model call; actual output/errors return to this expert '
        'turn. Use for sufficient routine implementation work to justify another model '
        'call, not a shell command you already wrote or a request to invent the missing algorithm. '
        'Worker charges reduce your remaining allowance; no added budget or hidden grader. '
        'Do not modify the same files while this call is pending. '
        'This tool includes real model inference and execution, so completion may take a few minutes. If your tool interface exposes it through functions.exec, use a dedicated cell with the exact first-line pragma // @exec: {"yield_time_ms": 60000, "max_output_tokens": 12000}, then await routine_worker and print its result. Do not mix other commands into that cell. If the cell is still running, call functions.wait on that SAME cell with yield_time_ms=60000 and max_tokens=12000. Do not poll every few seconds or repeat routine_worker. Waiting is not a new worker request. The cost monitor continues independently. Read the complete returned evidence; if your interface explicitly reports truncation, retrieve the full current evidence from the returned raw_evidence_file.path before claiming an observation. In a direct custom-tool interface without functions.exec, simply await the one routine_worker call; do not invent exec/wait tools.'),
    'inputSchema': SCHEMA,
}


def finite_cost(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def validate_work(arguments):
    jsonschema.validate(arguments, SCHEMA)
    if arguments['work_kind']=='experiment' and not arguments['hypothesis'].strip():
        raise ValueError('An experiment requires a stated hypothesis')
    for path in arguments['input_paths'] + arguments['output_paths']:
        p = PurePosixPath(path)
        if (not path.startswith('/app/') or '..' in p.parts
                or any(x in p.parts for x in ('.git', '.codex', '.agents', '.cursor'))):
            raise ValueError('Work paths must be ordinary files under /app')
    return copy.deepcopy(arguments)


class InTurnWorker:
    """One paid call per intervention; duplicate delivery never repeats execution.

    current_remaining excludes native expert and other already charged fees, but
    not this object's spent amount. reserve protects a small expert continuation.
    The callback must return actual usage even when its worker output is invalid.
    """
    def __init__(self, worker, current_remaining, worker_limit, reserve, emit):
        if not finite_cost(worker_limit) or not finite_cost(reserve):
            raise ValueError('Invalid delegation budget')
        self.worker = worker
        self.current_remaining = current_remaining
        self.worker_limit = worker_limit
        self.reserve = reserve
        self.emit = emit
        self.calls = {}
        self.records = []
        self.reservations = {}
        self.spent = 0.
        self.usage_complete = True
        self.closed = False
        self.lock = asyncio.Lock()

    async def invoke(self, call_id, arguments):
        # Validate before reserving a paid call. Hash full data, never only call ID.
        if not isinstance(call_id, str) or not call_id.strip():
            return {'status': 'invalid_work_order', 'model_calls': 0}
        try:
            work = validate_work(arguments)
        except (ValueError, TypeError, jsonschema.ValidationError):
            return {'status': 'invalid_work_order', 'model_calls': 0}
        digest = hashlib.sha256(json.dumps(work, sort_keys=True).encode()).hexdigest()
        async with self.lock:
            prior = self.calls.get(call_id)
            if prior:
                if prior[0] != digest:
                    return {'status': 'call_id_conflict', 'model_calls': 0}
                task = prior[1]
            else:
                if self.closed or self.calls or not self.usage_complete:
                    return {'status': 'worker_unavailable', 'model_calls': 0}
                available = self.current_remaining()
                if not finite_cost(available):
                    return {'status': 'unknown_remaining_budget', 'model_calls': 0}
                cap = min(self.worker_limit, max(0., available-self.spent-self.reserve))
                if cap <= 0:
                    return {'status': 'insufficient_remaining_budget', 'model_calls': 0}
                task = asyncio.create_task(self._execute(call_id, work, cap))
                self.calls[call_id] = (digest, task)
                self.reservations[call_id] = cap
        # Caller cancellation must not erase an already started paid worker.
        return await asyncio.shield(task)

    async def _execute(self, call_id, work, cap):
        record = {'call_id': call_id, 'work_order': work, 'allowance_usd': cap}
        self.emit('worker_requested', copy.deepcopy(record))
        try:
            result = await self.worker(work, cap)
        except Exception as exc:
            result = {'status': 'worker_failed', 'usage': {'cost_usd': None},
                      'error_type': type(exc).__name__, 'evidence': {}}
        if not isinstance(result, dict) or not isinstance(result.get('usage'), dict):
            result = {'status': 'worker_protocol_error', 'usage': {'cost_usd': None},
                      'evidence': {}}
        cost = result.get('usage', {}).get('cost_usd')
        if finite_cost(cost):
            self.spent += cost  # Includes overshoot, failed execution and invalid output.
        else:
            self.usage_complete = False
        record.update(result=copy.deepcopy(result), cost_known=finite_cost(cost))
        self.records.append(record)
        self.reservations.pop(call_id, None)
        self.emit('worker_finished', copy.deepcopy(record))
        return {'status': result.get('status', 'unknown'),
                'actual_cost_usd': cost if finite_cost(cost) else None,
                'usage_complete': self.usage_complete,
                'allowance_usd': cap, 'model_calls': result.get('model_calls',1),
                'evidence': copy.deepcopy(result.get('tool_evidence',result.get('evidence', {}))),
                'correctness_proven': False}

    async def settle(self):
        self.closed = True
        return await asyncio.gather(*(asyncio.shield(t) for _, t in self.calls.values()))

    @property
    def committed(self):
        return self.spent + sum(self.reservations.values()) if self.usage_complete else None
