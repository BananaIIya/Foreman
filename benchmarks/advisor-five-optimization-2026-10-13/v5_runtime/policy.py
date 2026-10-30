"""Execute complete expert handoffs without paying a model to relay commands."""
import copy
from v4_runtime.policy import (Budget, DISCOVER, PREPARE, WORKER, WORKER_SYSTEM,
    DECISION, DECISION_SCHEMA, ADVISOR, PATCH_SCHEMA as V4_SCHEMA,
    route, evidence_fingerprint, validate_patch as validate_v4)

PATCH_SCHEMA = copy.deepcopy(V4_SCHEMA)
PATCH_SCHEMA['required'].append('setup_commands')
PATCH_SCHEMA['properties']['setup_commands'] = {'type': 'array', 'items': {'type': 'string'}}
ADVISOR += ''' The protocol also requires setup_commands: an ordered list of dependency or environment setup commands (empty if unnecessary). An execution-only controller will install your complete files, run setup_commands then validation_commands in /app, and return real exit codes/stdout/stderr. There is no model inference in this command relay. Include invocation of your generator or program explicitly; writing a generator does not create its output. Include decisive public requirement checks and report their coverage in handoff. Shell variables and working-directory changes do not persist between commands; files and installed packages do. Commands have a 300-second limit each, and execution stops on the first failure. Keep the two lists together to at most eight commands. Never request hidden tests, benchmark answers, host access or other model calls. For missing essential evidence, status needs_context may include complete diagnostic files and commands that collect it. You may receive one bounded diagnostic return and then must provide the complete correction or honestly report what remains missing. Diagnostics are not a final answer. Do not claim any command succeeded before seeing its actual output.'''
DECISION += ''' At delivery, execution-only evidence contains actual command results rather than a worker model's interpretation. Evaluate it against the original task. Successful diagnostics do not constitute a solution; status needs_context must not be delivered. Failed commands or skipped commands are unresolved. If a complete specialist patch has executed successfully and substantively checks the requirements, no additional model implementation turn is required. Request further work only for a concrete missing requirement, failure or uncertain specialist assumption.'''

def validate_patch(patch):
    import jsonschema
    jsonschema.validate(patch, PATCH_SCHEMA)
    commands = patch['setup_commands'] + patch['validation_commands']
    if len(commands) > 8 or any(not c.strip() or len(c) > 20000 for c in commands):
        raise ValueError('Bounded execution needs at most eight nonempty commands')
    if patch['status'] == 'blocked' and (patch['files'] or commands):
        raise ValueError('Blocked response cannot modify or execute anything')
    # Reuse the existing full file/path validation, including diagnostic files.
    legacy = {k: v for k, v in patch.items() if k != 'setup_commands'}
    if patch['status'] == 'needs_context' and patch['files']:
        legacy['status'] = 'patch'
    validate_v4(legacy)
    if patch['status'] in ('patch', 'needs_context') and not patch['validation_commands']:
        raise ValueError('Executable handoff requires actual validation or diagnostic commands')
