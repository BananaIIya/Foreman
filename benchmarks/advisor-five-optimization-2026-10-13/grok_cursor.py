"""Pinned native Cursor runner; no task hints, advisor or grader feedback."""
import hashlib
import json
import os
import shlex
from pathlib import Path

from harbor.agents.installed.cursor_cli import CursorCli
from harbor.agents.installed.base import with_prompt_template

ROOT = Path(__file__).resolve().parent
PACKAGE = Path('/private/tmp/grok47-cursor-runtime/agent-cli-package-linux-x64-2026.10.01-e373342.tar.gz')
VERSION = '2026.10.01-e373342'
MODEL = 'grok-4.7-high'


class GrokCursor(CursorCli):
    def _model_arg(self):
        # This pinned CLI accepts explicit effort aliases, not bracket syntax.
        assert self.model_name == 'cursor/grok-4.7-high'
        assert self.options.reasoning_effort == 'high'
        return MODEL

    async def install(self, environment):
        assert self._model_arg() == MODEL
        expected = json.loads((ROOT / 'raw/preflight/cursor-package.json').read_text())
        assert hashlib.sha256(PACKAGE.read_bytes()).hexdigest() == expected['sha256']
        await environment.upload_file(PACKAGE, '/tmp/cursor-cli-package.tar.gz')
        command = (
            'set -e; mkdir -p "$HOME/.local/bin" '
            f'"$HOME/.local/share/cursor-agent/versions/{VERSION}"; '
            f'tar -xzf /tmp/cursor-cli-package.tar.gz --strip-components=1 -C "$HOME/.local/share/cursor-agent/versions/{VERSION}"; '
            f'ln -s "$HOME/.local/share/cursor-agent/versions/{VERSION}/cursor-agent" "$HOME/.local/bin/cursor-agent"; '
            '"$HOME/.local/bin/cursor-agent" --version'
        )
        result = await self.exec_as_agent(environment, command='bash -c ' + shlex.quote(command))
        assert result.stdout.strip() == VERSION, 'Cursor runtime version mismatch'
        models = await self.exec_as_agent(environment,
            command=f'"$HOME/.local/bin/cursor-agent" --model={shlex.quote(MODEL)} models',
            env={'CURSOR_API_KEY': os.environ['CURSOR_API_KEY']}, timeout_sec=90)
        assert 'grok-4.7-high -' in models.stdout, 'Grok 4.7 High unavailable; do not substitute.'
        safe = models.stdout.replace(os.environ['CURSOR_API_KEY'], '[REDACTED]')
        (self.logs_dir / 'cursor-models.txt').write_text(safe)
        (self.logs_dir / 'runtime.json').write_text(json.dumps({
            'version': VERSION, 'resolved_model': MODEL, 'advisor': False,
            'cli_package_sha256': expected['sha256'], 'staged_official_runtime': True,
            'egress_proxy': False, 'models_generation_requests': 0,
        }, indent=2) + '\n')

    @with_prompt_template
    async def run(self, instruction, environment, context):
        assert self._model_arg() == MODEL
        assert not self.mcp_servers and not self.skills_dir
        flags = self.build_cli_flags()
        command = ('export PATH="$HOME/.local/bin:$PATH"; '
                   f'cursor-agent --yolo --print --output-format=stream-json {flags} '
                   f'--model={shlex.quote(MODEL)} -- {shlex.quote(instruction)} '
                   f'2>&1 | stdbuf -oL tee /logs/agent/{self._OUTPUT_FILENAME}')
        (self.logs_dir / 'run-request.json').write_text(json.dumps({
            'resolved_model': MODEL, 'effort': 'high', 'fast': False,
            'instruction_sha256': hashlib.sha256(instruction.encode()).hexdigest(),
            'advisor': False, 'verifier_feedback': False, 'pipefail': True,
        }, indent=2) + '\n')
        await self.exec_as_agent(environment,
            command='bash -o pipefail -c ' + shlex.quote(command),
            env={'CURSOR_API_KEY': os.environ['CURSOR_API_KEY']})
        # Explicitly reject missing / erroneous native completion even if exit 0.
        await environment.download_file('/logs/agent/' + self._OUTPUT_FILENAME,
                                        self.logs_dir / self._OUTPUT_FILENAME)
        events = self._parse_stdout()
        inits = [e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init']
        results = [e for e in events if e.get('type') == 'result']
        assert len(inits) == 1 and 'grok' in inits[0]['model'].lower() and '4.7' in inits[0]['model']
        assert results and results[-1].get('is_error') is False, 'No successful native completion.'

