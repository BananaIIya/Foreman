"""Make component setup and ordinary worker batch failures observable."""
import shlex

SELF_CHECK_DIRECTORY = '/app/component-selfcheck'


def strict_shell(command):
    if not isinstance(command, str) or not command.strip():
        raise ValueError('Expected a nonempty worker command')
    return '/bin/bash -e -o pipefail -c ' + shlex.quote(command)


async def prepare_component_directory(run_command):
    command = 'mkdir -p -- ' + shlex.quote(SELF_CHECK_DIRECTORY)
    result = await run_command(command)
    return {'status': 'completed' if result.return_code == 0 else 'failed',
            'command': command, 'exit_code': result.return_code,
            'stdout': result.stdout, 'stderr': result.stderr,
            'model_calls': 0, 'correctness_proven': False}
