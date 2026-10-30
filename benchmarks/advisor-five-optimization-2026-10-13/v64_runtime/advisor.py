"""Official-style advisor: the executor does all work; the advisor sees its transcript and returns short advice.

Sol advisor: one no-tools native thread resumed across consultations, so earlier context is cached.
Grok advisor: one no-tools Cursor SDK answer per consultation with the transcript so far.
The advisor never touches the task workspace, hidden tests or grader output.
"""
import asyncio
import base64
import copy
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from hybrid_native import Session
from v2_runtime.sdk import advisor_turn as grok_advisor_turn
from .transcript import completed_items, render

MAX_CALLS = 3
TRANSCRIPT_CHARS = 60000

ADVISOR_POLICY = """You are the advisor to a lower-cost executor model that is doing a task in its own workspace. You see the original task and the executor's transcript so far (its messages, commands, outputs, edits). You have no tools and cannot see or change the workspace.
Reply with short, decisive guidance the executor can act on immediately: the most important correction or next step, the critical risk or wrong assumption you see in the evidence, and one to three concrete checks it should run. If the work looks correct, say so plainly and name the one check that would confirm it. Do not write full programs or restate the task. Keep the reply under about 250 words, in English.
No benchmark solutions, hidden tests or grader feedback are available; base advice on the task and the executor's observed evidence."""

EXECUTOR_POLICY = """You have an `advisor` tool backed by a stronger model. It takes an optional short question. When you call it, the advisor automatically sees the original task and your full transcript so far (messages, commands, outputs, edits); it cannot touch your workspace. You do all the work yourself.
Call the advisor:
- after initial orientation (reading the inputs and environment) and BEFORE substantive work, to check your approach and interpretation;
- when you are stuck, when results contradict your expectations, or before changing approach;
- before declaring the task complete: first write the required outputs and run your checks, then ask for a final review.
Give the advice serious weight. If you have concrete evidence that contradicts a specific claim, follow the evidence and say why. At most 3 advisor calls; do not call it for trivial steps."""

ADVISOR_TOOL = {
    'type': 'function',
    'name': 'advisor',
    'description': 'Consult a stronger advisor model that sees your full transcript so far. Short question (may be empty). At most 3 calls.',
    'inputSchema': {'type': 'object', 'additionalProperties': False,
                    'properties': {'question': {'type': 'string', 'maxLength': 2000,
                                                'description': 'What you want reviewed; may be empty.'}},
                    'required': ['question']},
}

FINAL_REVIEW_NUDGE = ('Before you finish: call the advisor now for a final review of your work, '
                      'then apply anything it finds and re-run your checks. If it confirms the work, you are done.')


def now():
    return datetime.now(timezone.utc).isoformat()


class NoToolsAdvisorSession(Session):
    """Sol advisor thread with every tool surface disabled (same switches as the V31 reviewer)."""
    async def rpc(self, method, params):
        if method in ('thread/start', 'thread/resume'):
            params = copy.deepcopy(params)
            if method == 'thread/start':
                params['baseInstructions'] = ADVISOR_POLICY
            features = params['config'].setdefault('features', {})
            for key in ('apps', 'plugins', 'shell_tool', 'unified_exec', 'multi_agent', 'browser_use',
                        'computer_use', 'image_generation', 'hooks', 'memories', 'skill_search',
                        'tool_suggest', 'sleep_tool'):
                features[key] = False
            params['config']['tools'] = {'view_image': False, 'web_search': False}
            params['config']['web_search'] = 'disabled'
        return await super().rpc(method, params)


class Advisor:
    def __init__(self, private, logs, executor_events, instruction, advisor_model, advisor_effort, images):
        self.private = Path(private)
        self.logs = Path(logs)
        self.executor_events = Path(executor_events)
        self.instruction = instruction
        self.model = advisor_model
        self.effort = advisor_effort
        self.images = images  # original task images (data URLs) for the first Sol consultation
        self.calls = []        # one record per completed or failed consultation
        self.seen_items = 0
        self.thread_id = None
        self.home = self.private / 'advisor-home'
        self.lock = asyncio.Lock()   # V63: one consultation at a time; numbers assigned at request time
        self.in_flight = None
        self.empty = self.private / 'advisor-empty'

    def setup(self, auth_json):
        self.home.mkdir(mode=0o700)
        self.empty.mkdir()
        shutil.copyfile(auth_json, self.home / 'auth.json')
        (self.home / 'auth.json').chmod(0o600)

    def record(self, event):
        event['at'] = now()
        with (self.logs / 'advisor-consultations.jsonl').open('a') as f:
            f.write(json.dumps(event) + '\n')

    def last_work_index(self):
        items = completed_items(self.executor_events)
        work = [i for i, it in enumerate(items) if it.get('type') in ('commandExecution', 'fileChange')]
        return work[-1] if work else -1, len(items)

    def needs_final_review(self):
        """Official guidance: a final call after writes and checks. True if no call followed the last work."""
        if len(self.calls) >= MAX_CALLS:
            return False
        ok = [c['items_seen'] for c in self.calls if c.get('ok')]
        if not ok:
            return True  # never consulted (including giving up before any command): always one review
        last_work, _ = self.last_work_index()
        return last_work >= 0 and max(ok) <= last_work

    async def handle(self, params):
        args = params.get('arguments') or {}
        if isinstance(args, str):
            args = json.loads(args or '{}')
        if params.get('tool') != 'advisor':
            return {'success': False, 'contentItems': [{'type': 'inputText', 'text': 'Unknown tool.'}]}
        async with self.lock:
            return await self._handle(args)

    async def _handle(self, args):
        if len(self.calls) >= MAX_CALLS:
            return {'success': False, 'contentItems': [{'type': 'inputText', 'text': 'Advisor limit reached (3). Continue with the advice you already have.'}]}
        items = completed_items(self.executor_events)
        call = len(self.calls) + 1
        self.in_flight = call
        folder = self.logs / 'advisor' / f'call-{call}'
        folder.mkdir(parents=True, exist_ok=False)
        question = (args.get('question') or '').strip()
        self.record({'event': 'advice_requested', 'call': call, 'question': question, 'items_seen': len(items)})
        try:
            if self.model == 'gpt-6.1-sol':
                text, usage = await self._sol(folder, items, question, call)
            else:
                text, usage = await self._grok(folder, items, question)
            self.seen_items = len(items)
            self.in_flight = None
            self.calls.append({'call': call, 'ok': True, 'items_seen': len(items), 'cost_usd': usage.get('cost_usd'),
                               'advice_chars': len(text)})
            (folder / 'advice.txt').write_text(text + '\n')
            self.record({'event': 'advice_returned', 'call': call, 'cost_usd': usage.get('cost_usd'), 'advice_chars': len(text)})
            return {'success': True, 'contentItems': [{'type': 'inputText', 'text': text}]}
        except Exception as exc:
            self.in_flight = None
            phase = folder / 'phase-result.json'
            billed = json.loads(phase.read_text())['usage'].get('cost_usd') if phase.exists() else None
            self.calls.append({'call': call, 'ok': False, 'items_seen': len(items), 'cost_usd': billed, 'error': type(exc).__name__})
            self.record({'event': 'advice_failed', 'call': call, 'error': f'{type(exc).__name__}: {exc}'[:500]})
            raise

    def abandon_in_flight(self):
        """A consultation still running when the executor finished: its spend is unknown, never zero."""
        if self.in_flight is not None:
            self.calls.append({'call': self.in_flight, 'ok': False, 'items_seen': None, 'cost_usd': None, 'error': 'abandoned_at_close'})
            self.record({'event': 'advice_abandoned', 'call': self.in_flight})
            self.in_flight = None

    async def _sol(self, folder, items, question, call):
        delta = render(items[self.seen_items:])
        if len(delta) > TRANSCRIPT_CHARS:
            delta = f'[{len(delta) - TRANSCRIPT_CHARS} earlier chars of this update omitted]…\n' + delta[-TRANSCRIPT_CHARS:]
        if self.thread_id is None:
            prompt = (f'ORIGINAL TASK:\n{self.instruction}\n\nEXECUTOR TRANSCRIPT SO FAR:\n{delta or "(no actions yet)"}'
                      f'\n\nEXECUTOR QUESTION: {question or "(none; review the work so far)"}')
            extra = self.images
        else:
            prompt = (f'EXECUTOR TRANSCRIPT SINCE YOUR LAST ADVICE:\n{delta or "(no new actions)"}'
                      f'\n\nEXECUTOR QUESTION: {question or "(none; review the work so far)"}')
            extra = None
        client = NoToolsAdvisorSession(self.home, folder, self.model, self.effort)
        try:
            await client.start()
            result = await client.turn(None, prompt, ADVISOR_POLICY, str(self.empty), thread_id=self.thread_id,
                                       extra_input=extra, no_tools=True)
        finally:
            await client.close()
        self.thread_id = result['thread_id']
        text = (result['evidence'].get('text') or '').strip()
        if not text:
            raise RuntimeError('Advisor returned no text')
        return text, result['usage']

    async def _grok(self, folder, items, question):
        transcript = render(items)
        if len(transcript) > TRANSCRIPT_CHARS:
            transcript = f'[{len(transcript) - TRANSCRIPT_CHARS} earlier chars omitted]…\n' + transcript[-TRANSCRIPT_CHARS:]
        earlier = '\n\n'.join(f'Advice {c["call"]}: ' + (self.logs / 'advisor' / f'call-{c["call"]}' / 'advice.txt').read_text()
                              for c in self.calls if c.get('ok'))
        prompt = (f'{ADVISOR_POLICY}\n\nORIGINAL TASK:\n{self.instruction}\n\nYOUR EARLIER ADVICE:\n{earlier or "(none)"}'
                  f'\n\nEXECUTOR TRANSCRIPT SO FAR:\n{transcript or "(no actions yet)"}\n\nEXECUTOR QUESTION: {question or "(none; review the work so far)"}')
        result = await grok_advisor_turn(folder, prompt, self.effort)
        text = (result['evidence'].get('text') or '').strip()
        if result['status'] != 'completed' or not text:
            raise RuntimeError('Grok advisor did not complete')
        return text, result['usage']


def load_images(environment_dir):
    """Original task images (at most two) for the advisor's first look; returns (input items, sha256s)."""
    images, shas = [], []
    for p in sorted(Path(environment_dir).rglob('*')):
        if p.suffix.lower() in ('.png', '.jpg', '.jpeg') and p.stat().st_size <= 4_000_000:
            mime = 'image/png' if p.suffix.lower() == '.png' else 'image/jpeg'
            data = p.read_bytes()
            images.append({'type': 'image', 'url': f'data:{mime};base64,' + base64.b64encode(data).decode(), 'detail': 'original'})
            shas.append(hashlib.sha256(data).hexdigest())
            if len(images) == 2:
                break
    return images, shas
