"""V64: the V63 official advisor pattern with Claude Haiku 5.5 as the executor.

HaikuSolo:        Haiku does the task alone.
HaikuWithAdvisor: Haiku does all the work and may call `advisor` (Sol high, no tools, sees Haiku's transcript,
                  at most 3 calls, one final-review nudge) exactly as in V63.
Haiku runs on the host through the Claude Agent SDK with every built-in tool disabled. Its only task tools
are `bash` and `view_image`, executed inside the Daytona task container via the isolated V60 relay.
User/global Claude settings are not loaded and the native Claude Code advisor is disabled.
No hidden tests, grader output or earlier solutions are given to any model.
"""
import base64
import contextlib
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
                              ServerToolUseBlock, TextBlock, ToolUseBlock, create_sdk_mcp_server, tool)
from remote_native_codex import RemoteNativeCodex
from v60_runtime.remote import run_task_command
from .advisor import Advisor, EXECUTOR_POLICY, FINAL_REVIEW_NUDGE, MAX_CALLS, load_images

MODEL = 'claude-haiku-5-5'
OUTPUT_CHARS = 30000

TASK_TOOLS_NOTE = """Your tools run inside the task's Linux container, whose working directory is /app:
- `bash`: run one bash command (cwd /app, 300-second limit). Files persist between calls; shell variables do not. Use here-documents to write files.
- `view_image`: look at an image file inside the container (for example a drawing referenced by the task).
You have no other tools and no access to the host machine."""


class HaikuSolo(RemoteNativeCodex):
    WITH_ADVISOR = False

    def populate_context_post_run(self, context):
        usage = self.logs_dir / 'v62-usage.json'
        if usage.exists():
            context.cost_usd = json.loads(usage.read_text()).get('total_cost_usd')

    async def run(self, instruction, environment, context):
        if self.model_name != 'anthropic/' + MODEL:
            raise RuntimeError('Unexpected benchmark model')
        task = environment.environment_dir.parent.name
        private = Path(tempfile.mkdtemp(prefix='v64-haiku-', dir='/private/tmp'))
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        events_path = self.logs_dir / 'executor-events.jsonl'
        events_path.write_text('')
        empty = private / 'host-cwd'
        empty.mkdir()

        def log_item(item):
            with events_path.open('a') as f:
                f.write(json.dumps({'method': 'item/completed', 'params': {'item': item}}) + '\n')

        advisor = None
        if self.WITH_ADVISOR:
            images, _ = load_images(environment.environment_dir)
            advisor = Advisor(private, self.logs_dir, events_path, instruction, 'gpt-6.1-sol', 'high', images)
            advisor.setup(self._resolve_auth_json_path())

        @tool('bash', 'Run one bash command inside the task container (cwd /app, 300-second limit).', {'command': str})
        async def bash(args):
            command = args['command']
            try:
                r = await run_task_command(self, environment, command)
            except Exception as exc:  # relay failure is reported to the executor, never fabricated as output
                log_item({'type': 'commandExecution', 'command': command, 'exitCode': None, 'aggregatedOutput': f'relay error {type(exc).__name__}'})
                return {'content': [{'type': 'text', 'text': f'Command relay failed ({type(exc).__name__}); nothing is known about its effect.'}], 'is_error': True}
            output = (r.stdout or '') + (('\n[stderr]\n' + r.stderr) if r.stderr else '')
            log_item({'type': 'commandExecution', 'command': command, 'exitCode': r.return_code, 'aggregatedOutput': output})
            if len(output) > OUTPUT_CHARS:
                output = f'[{len(output) - OUTPUT_CHARS} earlier chars omitted]\n' + output[-OUTPUT_CHARS:]
            return {'content': [{'type': 'text', 'text': f'exit code {r.return_code}\n{output}'}]}

        @tool('view_image', 'View an image file inside the task container, e.g. /app/schematic.png.', {'path': str})
        async def view_image(args):
            path = PurePosixPath(args['path'] if args['path'].startswith('/') else '/app/' + args['path'])
            suffix = path.suffix.lower()
            if suffix not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                return {'content': [{'type': 'text', 'text': 'Unsupported image type.'}], 'is_error': True}
            local = private / ('image' + suffix)
            try:
                await environment.download_file(str(path), local)
            except Exception as exc:
                return {'content': [{'type': 'text', 'text': f'Cannot read {path}: {type(exc).__name__}'}], 'is_error': True}
            mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}[suffix]
            log_item({'type': 'commandExecution', 'command': f'view_image {path}', 'exitCode': 0, 'aggregatedOutput': '[image shown to executor]'})
            return {'content': [{'type': 'image', 'data': base64.b64encode(local.read_bytes()).decode(), 'mimeType': mime}]}

        @tool('advisor', 'Consult a stronger advisor model that sees your full transcript so far. Short question (may be empty). At most 3 calls.', {'question': str})
        async def advise(args):
            result = await advisor.handle({'tool': 'advisor', 'arguments': {'question': args.get('question', '')}})
            text = ' '.join(c.get('text', '') for c in result['contentItems'])
            log_item({'type': 'dynamicToolCall', 'arguments': {'question': args.get('question', '')}, 'contentItems': result['contentItems'],
                      'success': result['success']})
            return {'content': [{'type': 'text', 'text': text}], **({} if result['success'] else {'is_error': True})}

        task_tools = [bash, view_image] + ([advise] if advisor else [])
        server = create_sdk_mcp_server('task', tools=task_tools)
        allowed = ['mcp__task__' + t.name for t in task_tools]
        append = TASK_TOOLS_NOTE + ('\n\n' + EXECUTOR_POLICY if advisor else '')
        options = ClaudeAgentOptions(
            model=MODEL, tools=[], mcp_servers={'task': server}, allowed_tools=allowed,
            setting_sources=[], cwd=str(empty), permission_mode='bypassPermissions',
            env={'CLAUDE_CODE_DISABLE_ADVISOR_TOOL': '1'},
            system_prompt={'type': 'preset', 'preset': 'claude_code', 'append': append})
        results, tool_names, server_tools, nudged = [], [], [], False

        async def drain(client):
            async for m in client.receive_response():
                if isinstance(m, AssistantMessage):
                    for b in m.content:
                        if isinstance(b, TextBlock) and b.text.strip():
                            log_item({'type': 'agentMessage', 'text': b.text})
                        elif isinstance(b, ToolUseBlock):
                            tool_names.append(b.name)
                        elif isinstance(b, ServerToolUseBlock):
                            server_tools.append(b.name)
                elif isinstance(m, ResultMessage):
                    results.append({'subtype': m.subtype, 'is_error': m.is_error, 'num_turns': m.num_turns,
                                    'total_cost_usd': m.total_cost_usd, 'model_usage': m.model_usage,
                                    'terminal_reason': m.terminal_reason, 'stop_reason': m.stop_reason})
        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(instruction)
                await drain(client)
                if advisor is not None and advisor.needs_final_review():
                    nudged = True
                    await client.query(FINAL_REVIEW_NUDGE)
                    await drain(client)
        finally:
            if advisor is not None:
                advisor.abandon_in_flight()
            # Session cost is cumulative in the SDK; the last result carries the whole session.
            executor_cost = results[-1]['total_cost_usd'] if results else None
            models = sorted({k for r in results for k in (r['model_usage'] or {})})
            calls = advisor.calls if advisor else []
            advisor_cost = sum(c['cost_usd'] for c in calls) if all(c.get('cost_usd') is not None for c in calls) else None
            complete = executor_cost is not None and (advisor is None or advisor_cost is not None)
            (self.logs_dir / 'v62-usage.json').write_text(json.dumps({
                'pattern': 'official_advisor' if advisor else 'executor_solo',
                'executor': {'model': MODEL, 'cost_usd': executor_cost, 'session_results': results,
                             'models_in_session': models, 'tool_calls': tool_names, 'native_server_tool_calls': server_tools},
                'advisor': ({'model': advisor.model, 'effort': advisor.effort, 'max_calls': MAX_CALLS, 'calls': calls,
                             'cost_usd': advisor_cost, 'final_review_nudge_sent': nudged} if advisor else None),
                'total_cost_usd': (executor_cost + (advisor_cost or 0)) if complete else None,
                'usage_complete': complete,
                'native_claude_advisor_disabled': True,
                'verifier_feedback': False,
                'cost_basis': 'standard token list-price equivalent (SDK list costBasis / Codex prices), not invoice; infrastructure excluded',
            }, indent=2) + '\n')
            with contextlib.suppress(Exception):
                await environment.upload_dir(self.logs_dir, str(self.environment_logs_dir))
            shutil.rmtree(private)


class HaikuWithAdvisor(HaikuSolo):
    WITH_ADVISOR = True
