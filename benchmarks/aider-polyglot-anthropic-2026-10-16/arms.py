"""Aider Polyglot, Anthropic models only. Four arms, same tools and base prompt:

OpusSolo            Opus 5.5 solves the exercise alone.
HaikuSolo           Haiku 5.5 solves it alone.
PlanThenWrite       Opus reads the exercise (read-only tools) and writes an implementation plan, no full code;
                    Haiku then writes the code from that plan (the "strong model plans, worker ghost-writes" idea).
HaikuNativeAdvisor  Haiku with Claude Code's own advisor tool, advisor model Opus 5.5 (Anthropic's pattern).

Models run on the host through the Claude Agent SDK with built-in tools off; tools run inside the Daytona task
container. User/global Claude settings and the account's claude.ai connectors are not loaded (connectors only since
2026-10-16; earlier runs carried them, see README). Tests are hidden from every model; no grader feedback.
"""
import json
import shlex
import tempfile
import shutil
from pathlib import Path

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
                              ServerToolUseBlock, TextBlock, ToolUseBlock, create_sdk_mcp_server, tool)
from harbor.agents.base import BaseAgent

OPUS, HAIKU = 'claude-opus-5-5', 'claude-haiku-5-5'
OUTPUT_CHARS = 20000

# Harness hygiene (added 2026-10-16 after capturing the CLI's requests). The SDK's child CLI uses the host's Claude login
# and otherwise loads the account's claude.ai connectors (~28k tokens of Gmail/Docs tools). They attach asynchronously, so
# they may appear on turn 1, mid-session (invalidating the prompt cache) or not at all. Without the desktop session's
# environment the CLI would also name each session with an extra call to the session model.
HARNESS_ENV = {'ENABLE_CLAUDEAI_MCP_SERVERS': 'false', 'CLAUDE_CODE_DISABLE_TERMINAL_TITLE': '1'}
# One-shot calls are never reused; the CLI's cache writes (1-hour TTL, 2x the input price) are pure overhead there.
ONE_SHOT_ENV = {'DISABLE_PROMPT_CACHING': '1'}
# The CLI's default reasoning setting, made explicit so it cannot drift with the environment.
THINKING = {'thinking': {'type': 'adaptive'}, 'effort': 'medium'}

BASE_PROMPT = """You are a coding agent solving a programming exercise. Your tools run inside the exercise's Linux container; the exercise files are in /app.
Read the instructions and the provided source files, implement the solution in the provided files (keep their names and public API), and check your work. The official tests are hidden; you may write and run your own checks. Finish with a short summary."""

PLANNER_PROMPT = """You are a senior engineer planning work for a cheaper model that will write the code. You can only list and read files.
Read the instructions and the provided source files, then reply with an implementation plan precise enough that a careful junior engineer can implement it correctly without further help:
which files and functions to edit, exact signatures and data structures, the algorithm step by step, edge cases and error handling the exercise expects, and pitfalls. Do NOT write the full implementation; short snippets only where a detail is easy to get wrong."""

NATIVE_ADVISOR_NOTE = """You have an `advisor` tool backed by a stronger model that sees this conversation. Call it after reading the exercise and before writing code, when stuck, and once before finishing."""


class ClaudeArm(BaseAgent):
    ARM = 'base'

    @staticmethod
    def name():
        return 'anthropic-arm'

    def version(self):
        return '1'

    async def setup(self, environment):
        # The task image ships an encrypted reference solution whose default password is public.
        # Grading never reads it, so remove it before any model runs.
        r = await environment.exec(command='rm -rf /app/.oracle && test ! -e /app/.oracle', cwd='/app', timeout_sec=60)
        if r.return_code:
            raise RuntimeError('Could not remove /app/.oracle before the run')

    def populate_context_post_run(self, context):
        usage = self.logs_dir / 'usage.json'
        if usage.exists():
            context.cost_usd = json.loads(usage.read_text()).get('total_cost_usd')

    # ---- container tools ----
    def _tools(self, environment, read_only=False):
        async def sh(command, timeout=300):
            r = await environment.exec(command='bash -lc ' + shlex.quote(command), cwd='/app', timeout_sec=timeout)
            out = (r.stdout or '') + (('\n[stderr]\n' + r.stderr) if r.stderr else '')
            if len(out) > OUTPUT_CHARS:
                out = f'[{len(out) - OUTPUT_CHARS} earlier chars omitted]\n' + out[-OUTPUT_CHARS:]
            return r.return_code, out

        @tool('bash', 'Run one bash command in the exercise container (cwd /app, 300-second limit).', {'command': str})
        async def bash(args):
            try:
                code, out = await sh(args['command'])
            except Exception as exc:
                return {'content': [{'type': 'text', 'text': f'Command failed to run ({type(exc).__name__}).'}], 'is_error': True}
            return {'content': [{'type': 'text', 'text': f'exit code {code}\n{out}'}]}

        @tool('list_files', 'List the exercise files under /app.', {})
        async def list_files(args):
            code, out = await sh("find /app -type f -not -path '*/.git/*' -not -path '*/node_modules/*' -not -path '*/.oracle/*' | sort | head -200")
            return {'content': [{'type': 'text', 'text': out}]}

        @tool('read_file', 'Read one file under /app.', {'path': str})
        async def read_file(args):
            path = args['path'] if args['path'].startswith('/') else '/app/' + args['path']
            if not path.startswith('/app/') or '..' in path:
                return {'content': [{'type': 'text', 'text': 'Only files under /app can be read.'}], 'is_error': True}
            code, out = await sh('cat -- ' + shlex.quote(path))
            return {'content': [{'type': 'text', 'text': out}], **({'is_error': True} if code else {})}

        return [list_files, read_file] if read_only else [bash]

    async def _session(self, model, system_prompt, prompt, tools, settings=None, disable_native_advisor=True):
        cwd = tempfile.mkdtemp(prefix='polyglot-host-')
        server = create_sdk_mcp_server('task', tools=tools)
        env = {**HARNESS_ENV, **({'CLAUDE_CODE_DISABLE_ADVISOR_TOOL': '1'} if disable_native_advisor else {})}
        options = ClaudeAgentOptions(model=model, tools=[], mcp_servers={'task': server}, strict_mcp_config=True,
                                     allowed_tools=['mcp__task__' + t.name for t in tools], setting_sources=[],
                                     cwd=cwd, permission_mode='bypassPermissions', env=env,
                                     settings=json.dumps(settings) if settings else None, system_prompt=system_prompt, **THINKING)
        texts, tool_calls, server_tools, results = [], [], [], []
        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(prompt)
                async for m in client.receive_response():
                    if isinstance(m, AssistantMessage):
                        for b in m.content:
                            if isinstance(b, TextBlock) and b.text.strip():
                                texts.append(b.text)
                            elif isinstance(b, ToolUseBlock):
                                tool_calls.append(b.name)
                            elif isinstance(b, ServerToolUseBlock):
                                server_tools.append(b.name)
                    elif isinstance(m, ResultMessage):
                        results.append({'subtype': m.subtype, 'num_turns': m.num_turns, 'total_cost_usd': m.total_cost_usd,
                                        'model_usage': m.model_usage, 'terminal_reason': m.terminal_reason})
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        last = results[-1] if results else {}
        return {'model': model, 'harness_env': env, 'cost_usd': last.get('total_cost_usd'), 'num_turns': last.get('num_turns'),
                'subtype': last.get('subtype'), 'tool_calls': tool_calls, 'server_tool_calls': server_tools,
                'per_model_cost_usd': {k: v.get('costUSD') for k, v in (last.get('model_usage') or {}).items()},
                'model_usage': last.get('model_usage'),
                'final_text': texts[-1] if texts else ''}

    def _write_usage(self, stages):
        costs = [s['cost_usd'] for s in stages]
        total = sum(costs) if all(isinstance(c, (int, float)) for c in costs) else None
        (self.logs_dir / 'usage.json').write_text(json.dumps({
            'arm': self.ARM, 'stages': stages, 'total_cost_usd': total, 'usage_complete': total is not None,
            'verifier_feedback': False,
            'cost_basis': 'Claude Agent SDK total_cost_usd (list-price equivalent), not invoice; infrastructure excluded'},
            indent=2) + '\n')


class OpusSolo(ClaudeArm):
    ARM = 'opus_solo'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        try:
            stages.append(await self._session(OPUS, BASE_PROMPT, instruction, self._tools(environment)))
        finally:
            self._write_usage(stages)


class HaikuSolo(ClaudeArm):
    ARM = 'haiku_solo'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        try:
            stages.append(await self._session(HAIKU, BASE_PROMPT, instruction, self._tools(environment)))
        finally:
            self._write_usage(stages)


class PlanThenWrite(ClaudeArm):
    ARM = 'opus_plans_haiku_writes'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        try:
            plan = await self._session(OPUS, PLANNER_PROMPT, instruction, self._tools(environment, read_only=True))
            stages.append(plan)
            (self.logs_dir / 'plan.md').write_text(plan['final_text'] + '\n')
            prompt = (f'{instruction}\n\n---\nIMPLEMENTATION PLAN FROM A SENIOR ENGINEER (follow it unless your own checks show it is wrong):\n'
                      f'{plan["final_text"]}')
            stages.append(await self._session(HAIKU, BASE_PROMPT, prompt, self._tools(environment)))
        finally:
            self._write_usage(stages)


class HaikuNativeAdvisor(ClaudeArm):
    ARM = 'haiku_native_opus_advisor'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        try:
            stages.append(await self._session(HAIKU, BASE_PROMPT + '\n\n' + NATIVE_ADVISOR_NOTE, instruction,
                                              self._tools(environment), settings={'advisorModel': OPUS},
                                              disable_native_advisor=False))
        finally:
            self._write_usage(stages)


# ---------------- Lean (no-tool) Opus arms, added after measuring the MCP-tool context overhead ----------------
import base64 as _b64
import re as _re

LEAN_SOLVER_PROMPT = """You are an expert programmer. You are given an exercise and its current source files. Reply with the COMPLETE new contents of every file you change, each as:
### FILE: <path relative to /app>
```
<entire file content>
```
Keep file names and the public API. The official tests are hidden. Output nothing except the file blocks."""

LEAN_PLANNER_PROMPT = PLANNER_PROMPT.replace('You can only list and read files.', 'The exercise files are included below; you have no tools.')

FILE_LIST = ("cd /app && find . -type f -not -path './.git/*' -not -path '*/node_modules/*' -not -path './.oracle/*' "
             "-not -path '*/build/*' -not -path '*/target/*' -not -path '*/.gradle/*' -size -60k | sed 's#^./##' | sort | head -40")


class LeanBase(ClaudeArm):
    async def _workspace_text(self, environment):
        r = await environment.exec(command='bash -lc ' + shlex.quote(FILE_LIST), cwd='/app', timeout_sec=60)
        parts = []
        for rel in [l for l in (r.stdout or '').splitlines() if l.strip()]:
            c = await environment.exec(command='bash -lc ' + shlex.quote('base64 -w0 -- ' + shlex.quote(rel)), cwd='/app', timeout_sec=60)
            try:
                body = _b64.b64decode(c.stdout or '').decode('utf-8')
            except Exception:
                continue
            parts.append(f'### FILE: {rel}\n```\n{body}\n```')
        return '\n\n'.join(parts)

    async def _lean(self, model, system_prompt, prompt):
        # No tools at all: the session carries only a few hundred tokens of harness context.
        cwd = tempfile.mkdtemp(prefix='polyglot-lean-')
        env = {**HARNESS_ENV, **ONE_SHOT_ENV, 'CLAUDE_CODE_DISABLE_ADVISOR_TOOL': '1'}
        options = ClaudeAgentOptions(model=model, tools=[], setting_sources=[], strict_mcp_config=True, cwd=cwd,
                                     permission_mode='bypassPermissions', env=env, system_prompt=system_prompt, max_turns=1,
                                     **THINKING)
        texts, results = [], []
        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(prompt)
                async for m in client.receive_response():
                    if isinstance(m, AssistantMessage):
                        texts += [b.text for b in m.content if isinstance(b, TextBlock)]
                    elif isinstance(m, ResultMessage):
                        results.append({'subtype': m.subtype, 'num_turns': m.num_turns, 'total_cost_usd': m.total_cost_usd,
                                        'model_usage': m.model_usage})
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        last = results[-1] if results else {}
        return {'model': model, 'lean': True, 'harness_env': env, 'cost_usd': last.get('total_cost_usd'), 'num_turns': last.get('num_turns'),
                'subtype': last.get('subtype'), 'tool_calls': [], 'server_tool_calls': [],
                'per_model_cost_usd': {k: v.get('costUSD') for k, v in (last.get('model_usage') or {}).items()},
                'model_usage': last.get('model_usage'), 'final_text': '\n'.join(texts)}


class OpusLeanOneShot(LeanBase):
    ARM = 'opus_lean_one_shot'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stages, written = [], []
        try:
            files = await self._workspace_text(environment)
            prompt = f'{instruction}\n\n---\nCURRENT FILES:\n{files}'
            (self.logs_dir / 'prompt.txt').write_text(prompt)
            stage = await self._lean(OPUS, LEAN_SOLVER_PROMPT, prompt)
            stages.append(stage)
            for path, body in _re.findall(r'### FILE: (\S+)\s*\n```[^\n]*\n(.*?)\n```', stage['final_text'], _re.S):
                if path.startswith('/') or '..' in path:
                    continue
                local = self.logs_dir / ('written-' + path.replace('/', '__'))
                local.write_text(body + '\n')
                await environment.exec(command='bash -lc ' + shlex.quote(f'mkdir -p "$(dirname {shlex.quote(path)})"'), cwd='/app', timeout_sec=60)
                await environment.upload_file(local, '/app/' + path)
                written.append(path)
            (self.logs_dir / 'written-files.json').write_text(json.dumps(written) + '\n')
        finally:
            self._write_usage(stages)


class LeanPlanThenWrite(LeanBase):
    ARM = 'opus_lean_plans_haiku_writes'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        try:
            files = await self._workspace_text(environment)
            plan = await self._lean(OPUS, LEAN_PLANNER_PROMPT, f'{instruction}\n\n---\nCURRENT FILES:\n{files}')
            stages.append(plan)
            (self.logs_dir / 'plan.md').write_text(plan['final_text'] + '\n')
            prompt = (f'{instruction}\n\n---\nIMPLEMENTATION PLAN FROM A SENIOR ENGINEER (follow it unless your own checks show it is wrong):\n'
                      f'{plan["final_text"]}')
            stages.append(await self._session(HAIKU, BASE_PROMPT, prompt, self._tools(environment)))
        finally:
            self._write_usage(stages)
