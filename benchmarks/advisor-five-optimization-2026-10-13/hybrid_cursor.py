"""Native Cursor phase runner; no reference answers or external score input."""
import hashlib
import json
import os
import shlex
from pathlib import Path
from grok_cursor import PACKAGE, VERSION
from hybrid_native import read_events
from routing import usage_cost


async def install_cursor(agent, environment):
    expected = 'a79726c6e644520e993970be4c45775a6889802b67abe461a677a53219ae28e8'
    assert hashlib.sha256(PACKAGE.read_bytes()).hexdigest() == expected
    await environment.upload_file(PACKAGE, '/tmp/cursor-cli-package.tar.gz')
    cmd = ('set -e; mkdir -p "$HOME/.local/bin" '
           f'"$HOME/.local/share/cursor-agent/versions/{VERSION}"; '
           f'tar -xzf /tmp/cursor-cli-package.tar.gz --strip-components=1 -C "$HOME/.local/share/cursor-agent/versions/{VERSION}"; '
           f'ln -sf "$HOME/.local/share/cursor-agent/versions/{VERSION}/cursor-agent" "$HOME/.local/bin/cursor-agent"; '
           '"$HOME/.local/bin/cursor-agent" --version')
    result = await agent.exec_as_agent(environment, command='bash -c ' + shlex.quote(cmd))
    assert result.stdout.strip() == VERSION


async def cursor_turn(agent, environment, folder, prompt, effort, session=None):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    remote = '/logs/agent/hybrid/' + folder.name + '.jsonl'
    model = 'grok-4.7-' + effort
    (folder / 'request.json').write_text(json.dumps({'model': model, 'effort': effort,
         'prompt': prompt, 'resume': session, 'verifier_feedback': False}, indent=2) + '\n')
    cmd = ('mkdir -p /logs/agent/hybrid; export PATH="$HOME/.local/bin:$PATH"; '
           'cursor-agent --yolo --print --output-format=stream-json '
           f'--model={shlex.quote(model)} ' + (f'--resume={shlex.quote(session)} ' if session else '') +
           f'-- {shlex.quote(prompt)} 2>&1 | stdbuf -oL tee {shlex.quote(remote)}')
    await agent.exec_as_agent(environment, command='bash -o pipefail -c ' + shlex.quote(cmd),
                              env={'CURSOR_API_KEY': os.environ['CURSOR_API_KEY']})
    await environment.download_file(remote, folder / 'cursor-cli.txt')
    events = read_events(folder / 'cursor-cli.txt')
    inits = [e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init']
    results = [e for e in events if e.get('type') == 'result']
    assert len(inits) == 1 and inits[0]['model'] == f'Grok 4.7 256K {effort.capitalize()}'
    assert len(results) == 1 and results[0].get('is_error') is False
    texts = ['\n'.join(t.get('text', '') for t in e.get('message', {}).get('content', []) if t.get('type') == 'text')
             for e in events if e.get('type') == 'assistant']
    commands = []
    for e in events:
        if e.get('type') != 'tool_call' or e.get('subtype') != 'completed':
            continue
        tool = e.get('tool_call', {}).get('shellToolCall')
        if tool:
            raw = tool.get('result', {}); out = raw.get('success', raw.get('failure', {}))
            commands.append({'command': tool.get('args', {}).get('command'),
                             'exit_code': out.get('exitCode'),
                             'output': out.get('interleavedOutput', out.get('stdout', ''))[-4000:]})
    usage = results[0].get('usage') or {}
    result = {'session_id': inits[0]['session_id'], 'status': 'completed',
              'native_model': inits[0]['model'], 'usage': {'native_usage': usage,
              'cost_usd': usage_cost('grok-4.7', usage, 'cursor')},
              'evidence': {'text': '\n\n'.join(texts), 'commands': commands[-10:], 'command_count': len(commands)}}
    (folder / 'phase-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
