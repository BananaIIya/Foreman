"""Ordered, observed remote execution. No model, scoring, or solution logic."""
import json

async def execute_commands(patch, run_command):
    commands = [('setup', c) for c in patch['setup_commands']]
    commands += [('validation', c) for c in patch['validation_commands']]
    observations = []
    for kind, command in commands:
        # Transport errors deliberately propagate; an HTTP error isn't task failure.
        output = await run_command(command)
        observations.append({
            'kind': kind, 'command': command, 'return_code': output.return_code,
            'stdout': (output.stdout or '')[:18000], 'stderr': (output.stderr or '')[:6000],
            'stdout_truncated': len(output.stdout or '') > 18000,
            'stderr_truncated': len(output.stderr or '') > 6000,
        })
        if output.return_code != 0:
            break
    skipped = [{'kind': kind, 'command': command} for kind, command in commands[len(observations):]]
    evidence = {
        'status': 'diagnostic_evidence' if patch['status'] == 'needs_context' else 'executed_patch',
        'specialist_status': patch['status'], 'checks': observations,
        'unexecuted_commands': skipped,
        'all_commands_succeeded': bool(observations) and not skipped and all(x['return_code'] == 0 for x in observations),
        'relevant_paths': [f['path'] for f in patch['files']],
        'requested_evidence': patch['requested_evidence'], 'handoff': patch['handoff'],
        'no_model_call': True, 'hidden_grader_used': False,
    }
    return {'usage': {'cost_usd': 0.0}, 'status': 'completed', 'no_model_call': True,
            'execution': evidence, 'evidence': {'text': json.dumps(evidence, ensure_ascii=False)}}
