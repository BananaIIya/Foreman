"""Define a concrete cheap work order instead of an unbounded initial draft."""
PLAN = '''You are the cheap decision model preparing one work order for a weaker worker. You have the original assignment and observed runtime/file metadata. Select ONE small, reliable piece of useful work that can be executed in at most two short commands without solving the uncertain specialist problem. The work must leave reusable observed input data, a straightforward required output scaffold, or an executable requirement check. Do not ask the worker to build a speculative full solver, infer a difficult scientific interpretation, or verify the entire assignment. Do not duplicate observations already in the supplied metadata. If no safe independent implementation exists, ask the worker to identify a concrete missing observation or requirement from the supplied task excerpt; include that excerpt in the work order. Never fabricate task facts or answers.
The worker will receive only this work order, not the complete original assignment. Include exact original paths and the necessary original requirement excerpt so it is self-contained. Specify what successful execution would show and what remains for the specialist. Do not include generated code, shell commands, benchmark answers, historical results, hidden tests, or other model calls. You may choose a routine dependency setup step only when it is essential and clearly supported by the observed environment. Avoid guessing package APIs. Return the required compact JSON. The full assignment is retained by the controller for later routing and expert work.'''

PLAN_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['objective', 'input_paths', 'requirement_excerpt', 'expected_evidence', 'specialist_boundary'],
    'properties': {
        'objective': {'type': 'string', 'minLength': 1, 'maxLength': 800},
        'input_paths': {'type': 'array', 'maxItems': 12, 'items': {'type': 'string'}},
        'requirement_excerpt': {'type': 'string', 'maxLength': 1800},
        'expected_evidence': {'type': 'string', 'minLength': 1, 'maxLength': 800},
        'specialist_boundary': {'type': 'string', 'minLength': 1, 'maxLength': 800},
    },
}

INITIAL_WORK = '''Execute only the supplied small work order. It is one step within a larger assignment managed by the decision model. Do not attempt the larger specialist task or invent missing requirements. Use at most two focused commands and keep their output concise. Prefer existing reliable libraries or straightforward file inspection over a new general framework. Preserve useful observations or code in /app and report exact paths, actual results, and any failure. If a needed package is unavailable under an externally managed Python installation, use a task-local virtual environment rather than modifying system Python. If the work order itself requires uncertain specialist reasoning, return needs_advisor promptly with the precise uncertainty. A completed status means only this work order is done; the controller will separately assess the full original assignment. No hidden grader, benchmark search, other model, or human approval is available.'''
