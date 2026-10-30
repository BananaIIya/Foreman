"""Commit0 library generation with Anthropic models.

ReferenceOracle    no model: copies the reference implementation in, to validate the image and the verifier.
OpusSolo           Opus 5.5 implements the library alone (bash tool in the container, budget cap).
HaikuSolo          Haiku 5.5 implements it alone (bash tool, budget cap).
LeanPlanWrite      Lean Opus (no tools; stubs and tests pasted) writes a per-function implementation spec ->
                   Haiku implements and runs the tests -> if tests still fail, a lean Opus review of the failures
                   and current code -> Haiku fixes. At most 2 review rounds. Every stage's cost is recorded.
Models run on the host via the Claude Agent SDK with built-in tools off; user/global Claude settings and the account's
claude.ai connectors are not loaded (connectors only since 2026-10-16; the tinydb canary carried them) and the native
Claude Code advisor is disabled. The verifier restores the original tests and scores pass fraction.
"""
import base64
import json
import shlex
import shutil
import tempfile

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
                              ServerToolUseBlock, TextBlock, ToolUseBlock, create_sdk_mcp_server, tool)
from harbor.agents.base import BaseAgent

OPUS, HAIKU = 'claude-opus-5-5', 'claude-haiku-5-5'
LIB_INFO = {  # src_dir, test_dir, reference commit
    'simpy': ('src/simpy', 'tests', '937f488d8524c15006184979c1977e370788cac7'),
    'tinydb': ('tinydb', 'tests', None),
    'voluptuous': ('voluptuous', 'voluptuous/tests', None),
}
OUTPUT_CHARS = 20000
OPUS_CAP, HAIKU_CAP = 8.0, 3.0

# Harness hygiene (added 2026-10-16 after capturing the CLI's requests). The SDK's child CLI uses the host's Claude login
# and otherwise loads the account's claude.ai connectors (~28k tokens of Gmail/Docs tools). They attach asynchronously, so
# they may appear on turn 1, mid-session (invalidating the prompt cache) or not at all. Without the desktop session's
# environment the CLI would also name each session with an extra call to the session model.
HARNESS_ENV = {'ENABLE_CLAUDEAI_MCP_SERVERS': 'false', 'CLAUDE_CODE_DISABLE_TERMINAL_TITLE': '1'}
# One-shot calls are never reused; the CLI's cache writes (1-hour TTL, 2x the input price) are pure overhead there.
ONE_SHOT_ENV = {'DISABLE_PROMPT_CACHING': '1'}
# The CLI's default reasoning setting, made explicit so it cannot drift with the environment.
THINKING = {'thinking': {'type': 'adaptive'}, 'effort': 'medium'}

BASE_PROMPT = """You are a coding agent. Your tools run inside the task's Linux container; the repository is in /app.
Implement what the task asks by editing files with the bash tool (use here-documents or python to write files), run the tests to check your work, and finish with a short summary."""

PLANNER_PROMPT = """You are a senior engineer writing the implementation spec for a cheaper model that will write all of the code. You have no tools; the stubbed source files and the tests are included below.
For every function, method and class that needs implementing, give: the file, the exact signature, what it must do (behaviour, return values, state changes), the algorithm in concise steps, edge cases and exceptions the tests expect, and interactions with other parts of the library. Order the work so dependencies come first.
Do NOT write the full implementation; short snippets only where a detail is easy to get wrong. Be precise and complete: the implementer cannot ask you questions."""

REVIEW_PROMPT = """You are a senior engineer reviewing a cheaper model's implementation. You have no tools. You get the task, the failing test report and the current source. Diagnose the root causes of the failures and reply with precise fix instructions per file and function (what is wrong, what it must do instead). Short snippets only where a detail is easy to get wrong. Do not rewrite whole files."""


def _lib(environment):
    name = environment.environment_dir.parent.name.removeprefix('commit0-')
    return name, LIB_INFO[name]


class Commit0Agent(BaseAgent):
    ARM = 'base'

    @staticmethod
    def name():
        return 'commit0-anthropic'

    def version(self):
        return '1'

    async def setup(self, environment):
        return None

    def populate_context_post_run(self, context):
        u = self.logs_dir/'usage.json'
        if u.exists():
            context.cost_usd = json.loads(u.read_text()).get('total_cost_usd')

    async def sh(self, environment, command, timeout=300):
        r = await environment.exec(command='bash -lc ' + shlex.quote(command), cwd='/app', timeout_sec=timeout)
        out = (r.stdout or '') + (('\n[stderr]\n' + r.stderr) if r.stderr else '')
        return r.return_code, out

    def bash_tool(self, environment):
        @tool('bash', 'Run one bash command in the task container (cwd /app, 600-second limit).', {'command': str})
        async def bash(args):
            try:
                code, out = await self.sh(environment, args['command'], timeout=600)
            except Exception as exc:
                return {'content': [{'type': 'text', 'text': f'Command failed to run ({type(exc).__name__}).'}], 'is_error': True}
            if len(out) > OUTPUT_CHARS:
                out = f'[{len(out) - OUTPUT_CHARS} earlier chars omitted]\n' + out[-OUTPUT_CHARS:]
            return {'content': [{'type': 'text', 'text': f'exit code {code}\n{out}'}]}
        return bash

    async def session(self, model, system_prompt, prompt, tools, cap):
        cwd = tempfile.mkdtemp(prefix='commit0-host-')
        kw = {}
        if tools:
            kw = {'mcp_servers': {'task': create_sdk_mcp_server('task', tools=tools)},
                  'allowed_tools': ['mcp__task__' + t.name for t in tools]}
        env = {**HARNESS_ENV, **({} if tools else ONE_SHOT_ENV), 'CLAUDE_CODE_DISABLE_ADVISOR_TOOL': '1'}
        options = ClaudeAgentOptions(model=model, tools=[], setting_sources=[], strict_mcp_config=True, cwd=cwd,
                                     permission_mode='bypassPermissions', env=env, system_prompt=system_prompt,
                                     max_budget_usd=cap, **({} if tools else {'max_turns': 1}), **THINKING, **kw)
        texts, calls, server_tools, results = [], [], [], []
        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(prompt)
                async for m in client.receive_response():
                    if isinstance(m, AssistantMessage):
                        for b in m.content:
                            if isinstance(b, TextBlock) and b.text.strip():
                                texts.append(b.text)
                            elif isinstance(b, ToolUseBlock):
                                calls.append(b.name)
                            elif isinstance(b, ServerToolUseBlock):
                                server_tools.append(b.name)
                    elif isinstance(m, ResultMessage):
                        results.append({'subtype': m.subtype, 'num_turns': m.num_turns, 'total_cost_usd': m.total_cost_usd,
                                        'model_usage': m.model_usage})
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        last = results[-1] if results else {}
        return {'model': model, 'lean': not tools, 'harness_env': env, 'cap_usd': cap, 'cost_usd': last.get('total_cost_usd'),
                'num_turns': last.get('num_turns'), 'subtype': last.get('subtype'), 'tool_calls': len(calls),
                'server_tool_calls': server_tools, 'model_usage': last.get('model_usage'), 'final_text': '\n'.join(texts)}

    async def read_tree(self, environment, path, limit_chars):
        code, listing = await self.sh(environment, f"find {shlex.quote(path)} -name '*.py' -type f | sort")
        parts, used = [], 0
        for f in [l for l in listing.splitlines() if l.strip()]:
            _, b64 = await self.sh(environment, 'base64 -w0 -- ' + shlex.quote(f))
            try:
                body = base64.b64decode(b64.strip()).decode('utf-8')
            except Exception:
                continue
            block = f'### FILE: {f}\n```python\n{body}\n```'
            if used + len(block) > limit_chars:
                parts.append(f'### FILE: {f}\n[omitted: size limit]')
                continue
            parts.append(block)
            used += len(block)
        return '\n\n'.join(parts)

    async def test_report(self, environment, test_dir):
        cmd = (f'timeout 900 pytest {shlex.quote(test_dir)} -p no:cacheprovider -q --maxfail=200 2>&1 | tail -c 12000; '
               f'pytest {shlex.quote(test_dir)} -p no:cacheprovider -q --co 2>/dev/null | tail -1')
        _, out = await self.sh(environment, cmd, timeout=1200)
        return out

    def write_usage(self, stages, extra=None):
        costs = [s['cost_usd'] for s in stages]
        total = sum(costs) if costs and all(isinstance(c, (int, float)) for c in costs) else (0.0 if not stages else None)
        (self.logs_dir/'usage.json').write_text(json.dumps({
            'arm': self.ARM, 'stages': stages, 'total_cost_usd': total, 'usage_complete': total is not None,
            **(extra or {}), 'verifier_feedback': False,
            'cost_basis': 'Claude Agent SDK total_cost_usd (list-price equivalent), not invoice; infrastructure excluded'},
            indent=2) + '\n')


class ReferenceOracle(Commit0Agent):
    ARM = 'reference_oracle'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        lib, (src, _tests, _ref) = _lib(environment)
        meta = (environment.environment_dir.parent/'task.toml').read_text()
        ref = meta.split('reference_commit = "')[1].split('"')[0]
        code, out = await self.sh(environment, (
            f'cd /tmp && curl -sSL https://github.com/commit-0/{lib}/archive/{ref}.tar.gz -o ref.tgz && '
            f'mkdir -p ref && tar -xzf ref.tgz -C ref --strip-components 1 && rm -rf /app/{src} && cp -a ref/{src} /app/{src}'), timeout=600)
        (self.logs_dir/'oracle.txt').write_text(f'exit {code}\n{out[-4000:]}\n')
        self.write_usage([])


class OpusSolo(Commit0Agent):
    ARM = 'opus_solo'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        try:
            stages.append(await self.session(OPUS, BASE_PROMPT, instruction, [self.bash_tool(environment)], OPUS_CAP))
        finally:
            self.write_usage(stages)


class HaikuSolo(Commit0Agent):
    ARM = 'haiku_solo'

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        try:
            stages.append(await self.session(HAIKU, BASE_PROMPT, instruction, [self.bash_tool(environment)], HAIKU_CAP))
        finally:
            self.write_usage(stages)


class LeanPlanWrite(Commit0Agent):
    ARM = 'lean_opus_plans_haiku_writes'
    REVIEW_ROUNDS = 2

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        lib, (src, tests, _ref) = _lib(environment)
        stages, rounds = [], []
        try:
            stubs = await self.read_tree(environment, src, 260000)
            test_text = await self.read_tree(environment, tests, 160000) if not tests.startswith(src) else '(tests are inside the source tree above)'
            plan = await self.session(OPUS, PLANNER_PROMPT, f'{instruction}\n\n---\nSTUBBED SOURCE:\n{stubs}\n\n---\nTESTS:\n{test_text}', [], OPUS_CAP)
            stages.append(plan)
            (self.logs_dir/'plan.md').write_text(plan['final_text'] + '\n')
            stages.append(await self.session(HAIKU, BASE_PROMPT,
                                             f'{instruction}\n\n---\nIMPLEMENTATION SPEC FROM A SENIOR ENGINEER (follow it unless the tests show it is wrong):\n{plan["final_text"]}',
                                             [self.bash_tool(environment)], HAIKU_CAP))
            for r in range(self.REVIEW_ROUNDS):
                report = await self.test_report(environment, tests)
                done = (' passed' in report and ' failed' not in report and ' error' not in report)
                rounds.append({'round': r, 'all_passed_before_review': done, 'report_tail': report[-600:]})
                if done:
                    break
                current = await self.read_tree(environment, src, 200000)
                review = await self.session(OPUS, REVIEW_PROMPT,
                                            f'{instruction}\n\n---\nFAILING TEST REPORT:\n{report}\n\n---\nCURRENT SOURCE:\n{current}', [], OPUS_CAP)
                stages.append(review)
                (self.logs_dir/f'review-{r}.md').write_text(review['final_text'] + '\n')
                stages.append(await self.session(HAIKU, BASE_PROMPT,
                                                 f'{instruction}\n\n---\nThe tests still fail. FIX INSTRUCTIONS FROM A SENIOR ENGINEER:\n{review["final_text"]}',
                                                 [self.bash_tool(environment)], HAIKU_CAP))
        finally:
            self.write_usage(stages, {'review_rounds': rounds})
