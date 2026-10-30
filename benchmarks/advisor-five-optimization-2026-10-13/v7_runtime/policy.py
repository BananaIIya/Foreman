"""Execute complete expert handoffs without paying a model to relay commands."""
import copy
from v4_runtime.policy import (Budget as V4Budget, DISCOVER, PREPARE, WORKER, WORKER_SYSTEM,
    DECISION, DECISION_SCHEMA, ADVISOR, PATCH_SCHEMA as V4_SCHEMA,
    route, evidence_fingerprint, validate_patch as validate_v4)

class Budget(V4Budget):
    def advisor_allowance(self):
        if not self.usage_complete or self.advisor_calls >= 2:
            return 0.0
        # Literal execution is free of model inference. Reserve a cheap delivery
        # gate, not a mandatory Grok integration that this architecture removed.
        reserve=min(.005,self.baseline*.03)
        return max(0.0,min(self.baseline*.45,self.ceiling-self.spent-reserve))

PATCH_SCHEMA = copy.deepcopy(V4_SCHEMA)
PATCH_SCHEMA['required'].append('setup_commands')
PATCH_SCHEMA['properties']['setup_commands'] = {'type': 'array', 'items': {'type': 'string'}}
ADVISOR += ''' The protocol also requires setup_commands: an ordered list of dependency or environment setup commands (empty if unnecessary). An execution-only controller will install your complete files, run setup_commands then validation_commands in /app, and return real exit codes/stdout/stderr. There is no model inference in this command relay. Include invocation of your generator or program explicitly; writing a generator does not create its output. Include decisive public requirement checks and report their coverage in handoff. Shell variables and working-directory changes do not persist between commands; files and installed packages do. Commands have a 300-second limit each, and execution stops on the first failure. Keep the two lists together to at most eight commands. Never request hidden tests, benchmark answers, host access or other model calls. For missing essential evidence, status needs_context may include complete diagnostic files and commands that collect it. You may receive one bounded diagnostic return and then must provide the complete correction or honestly report what remains missing. Diagnostics are not a final answer. Do not claim any command succeeded before seeing its actual output.'''
DECISION += ''' At delivery, execution-only evidence contains actual command results rather than a worker model's interpretation. Evaluate it against the original task. Successful diagnostics do not constitute a solution; status needs_context must not be delivered. Failed commands or skipped commands are unresolved. If a complete specialist patch has executed successfully and substantively checks the requirements, no additional model implementation turn is required. Request further work only for a concrete missing requirement, failure or uncertain specialist assumption.'''
DECISION += ''' An advisor contract must transfer executable implementation responsibility for the uncertain part. Do not ask only for interpretation, dimensions, a verdict, or an outline that leaves a weaker worker to reconstruct the difficult implementation. For a generated artifact, require a complete runnable generator producing that artifact plus invocation and semantic checks. Keep the named uncertainty focused, but require its resolution in working code.'''
ADVISOR += ''' A routing question explains WHY expert help is needed; it never waives the executable-file protocol. status patch MUST contain at least one complete file and at least one command. Do not answer only the narrow interpretation question in handoff while leaving files empty. For generated artifacts, implement the complete generator and include its invocation. If you can only supply observations first, label them needs_context and collect the missing evidence with explicit commands.'''
ADVISOR += ''' The payload includes runtime_environment collected from the actual container at this checkpoint. Use its installed versions and local wheel inventory to choose compatible code and setup commands. Do not invent a package version pin absent from this environment. If the original task explicitly requires an unavailable version, report that real constraint instead of silently replacing it. Prefer already installed dependencies; when installing, use quiet output and then print the relevant actual version. This inventory is environment metadata, not task solution evidence. Original task requirements still govern.'''
DISCOVER += ''' The controller supplies observed runtime_environment and a manifest of original images available directly to the decision model and Sol advisor. Do not spend your few commands re-listing those package versions or trying to decode an already attached drawing into text. For visual specialist interpretation, report the concrete uncertainty and the existing image path. Collect only genuinely missing task evidence.'''
DISCOVER += ''' A fresh /app file inventory is already included. If the original task, inventory and runtime metadata establish the specialist uncertainty and exact input paths, return needs_advisor immediately with commands empty. Do not pay for a shell round-trip merely to confirm the same filenames or package metadata. Execute commands only for material missing observations that you can collect routinely.'''
ADVISOR += ''' Use runtime_environment.python_executable in Python setup/execution commands; do not assume a bare python alias exists. Keep a needs_context diagnostic concise: use available scientific parsers/libraries and collect only the observations needed for your final implementation, rather than building an extensive framework. Leave enough of the stated cost target for the final diagnostic return.'''
ADVISOR += ''' If Python is externally managed and additional packages are essential, create a task-local virtual environment and invoke its interpreter explicitly. Do not assume pip can modify the system interpreter. Use installed scientific libraries when they suffice.'''
WORKER += ''' Use the observed runtime_environment.python_executable for Python commands. The controller's file inventory and package metadata are actual observations, not guesses requiring repeated shell confirmation.'''

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
