"""Offline tests: no model calls, no Daytona."""
import asyncio
import json
import shutil
import subprocess

import pytest

import fusion_arms as fa
import launch


class FakeContainer:
    def __init__(self, changed=('/app/a.py',)):
        self.plain_calls, self.changed = [], list(changed)

    async def run(self, command):
        return 0, 'ok'

    async def plain(self, command, timeout=60):
        self.plain_calls.append(command)
        return 0, '\n'.join(self.changed) if command.startswith('find ') else ''

    async def image(self, path):
        return b''


class FakeClient:
    instances = []

    def __init__(self, options):
        self.options, self.queries, self.connected = options, [], False
        FakeClient.instances.append(self)

    async def connect(self):
        self.connected = True

    async def query(self, prompt):
        self.queries.append(prompt)

    async def interrupt(self):
        pass

    async def disconnect(self):
        self.connected = False


def test_session_summary_uses_last_cumulative_result():
    s = fa.session_summary('m', [{'total_cost_usd': 0.1, 'num_turns': 3, 'model_usage': {'m': {'costUSD': 0.1}}},
                                 {'total_cost_usd': 0.25, 'num_turns': 4, 'model_usage': {'m': {'costUSD': 0.25}}}])
    assert s['cost_usd'] == 0.25 and s['num_turns'] == 7 and s['queries'] == 2 and s['per_model_cost_usd'] == {'m': 0.25}


def test_sidekick_persistent_session_cost_deltas_and_facts(tmp_path, monkeypatch):
    FakeClient.instances.clear()
    monkeypatch.setattr(fa, 'ClaudeSDKClient', FakeClient)
    script = iter([(['STATUS: done\nDONE: explored'], {'subtype': 'success', 'num_turns': 5, 'total_cost_usd': 0.02}),
                   (['STATUS: partial\nDONE: tests'], {'subtype': 'success', 'num_turns': 7, 'total_cost_usd': 0.05})])
    log = fa.EventLog(tmp_path / 'side.jsonl')

    async def fake_drain(client, log_, role):
        log_.commands.append(('pytest -q', 1))
        return next(script)
    monkeypatch.setattr(fa, 'drain', fake_drain)
    side = fa.Sidekick(FakeContainer(), log, 'THE TASK TEXT', tmp_path)

    async def go():
        r1 = await side.delegate('explore the repo')
        r2 = await side.delegate('run the tests')
        await side.close()
        return r1, r2
    r1, r2 = asyncio.run(go())
    assert len(FakeClient.instances) == 1, 'one persistent sidekick session for the whole run'
    q = FakeClient.instances[0].queries
    assert 'THE TASK TEXT' in q[0] and 'explore the repo' in q[0]
    assert q[1] == 'run the tests', 'follow-up briefs carry no repeated context'
    d = side.summary()['delegations']
    assert [x['cost_usd'] for x in d] == [0.02, pytest.approx(0.03)]
    assert side.summary()['cost_usd'] == 0.05
    assert 'SIDEKICK REPORT (delegation #2)' in r2 and 'STATUS: partial' in r2
    assert '[exit 1] pytest -q' in r2 and '/app/a.py' in r2 and '1 with nonzero' in r2
    assert d[0]['commands'] == 1 and d[1]['commands'] == 1


def test_report_flags_missing_result_and_failed_listing():
    text = fa.delegation_report(3, [], None, [('x', None)], None, True)
    assert 'returned no report' in text and 'unknown (listing failed)' in text
    assert 'ended without a result message' in text and 'interrupted' in text and '[exit None] x' in text


def test_options_are_isolated():
    tools = fa.task_tools(FakeContainer(), None)
    o = fa.options_for(fa.HAIKU, 'p', tools, '/tmp', 1.0)
    assert o.tools == [] and o.strict_mcp_config and o.setting_sources == []
    assert o.allowed_tools == ['mcp__task__bash', 'mcp__task__view_image']
    assert o.env['ENABLE_CLAUDEAI_MCP_SERVERS'] == 'false' and o.env['CLAUDE_CODE_DISABLE_ADVISOR_TOOL'] == '1'
    assert o.effort == fa.EFFORT and o.max_budget_usd == 1.0 and o.settings is None
    a = fa.options_for(fa.HAIKU, 'p', tools, '/tmp', 1.0, advisor_model=fa.OPUS)
    assert 'CLAUDE_CODE_DISABLE_ADVISOR_TOOL' not in a.env and json.loads(a.settings) == {'advisorModel': fa.OPUS}
    assert 'CLAUDE_CODE_DISABLE_ADVISOR_TOOL' in fa.HARNESS_ENV, 'other arms keep the native advisor disabled'


@pytest.mark.skipif(not shutil.which('timeout'), reason='no coreutils timeout on this host (checked in the container)')
def test_command_deadline(monkeypatch):
    monkeypatch.setattr(fa, 'COMMAND_TIMEOUT_SEC', 1)
    r = subprocess.run(['bash', '-c', fa.wrapped('sleep 5; echo never')], capture_output=True, text=True, timeout=20)
    assert r.returncode == 124 and 'never' not in r.stdout and 'deadline exceeded' in r.stderr
    r = subprocess.run(['bash', '-c', fa.wrapped("echo 'quoted' && exit 3")], capture_output=True, text=True, timeout=20)
    assert r.returncode == 3 and 'quoted' in r.stdout


def test_sidekick_recovers_from_a_broken_session(tmp_path, monkeypatch):
    class Breaking(FakeClient):
        async def query(self, prompt):
            self.queries.append(prompt)
            if len(FakeClient.instances) == 1:
                raise ConnectionError('cli died')
    FakeClient.instances.clear()
    monkeypatch.setattr(fa, 'ClaudeSDKClient', Breaking)

    async def fake_drain(client, log_, role):
        return ['STATUS: done'], {'subtype': 'success', 'num_turns': 2, 'total_cost_usd': 0.01}
    monkeypatch.setattr(fa, 'drain', fake_drain)
    side = fa.Sidekick(FakeContainer(), fa.EventLog(tmp_path / 's.jsonl'), 'TASK TEXT', tmp_path)

    async def go():
        return await side.delegate('first'), await side.delegate('second')
    r1, r2 = asyncio.run(go())
    assert 'session failed (ConnectionError)' in r1 and 'STATUS: done' in r2
    assert len(FakeClient.instances) == 2, 'a fresh session after the broken one'
    assert 'TASK TEXT' in FakeClient.instances[1].queries[0], 'the fresh session gets the task text again'
    s = side.summary()
    assert s['sessions'] == 2 and s['attempts'] == 2
    assert s['cost_usd'] is None, 'a used session with no result makes the cost unknown, not zero'
    assert [d['error'] for d in s['delegations']] == ['ConnectionError', None]


def test_fusion_usage_is_incomplete_when_sidekick_cost_unknown(tmp_path):
    arm = fa.Fusion.__new__(fa.Fusion)
    arm.logs_dir = tmp_path
    arm._write_usage({'lead': {'cost_usd': 0.5}, 'sidekick': {'cost_usd': None}}, 0, 'completed')
    u = json.loads((tmp_path / 'usage.json').read_text())
    assert u['total_cost_usd'] is None and u['usage_complete'] is False


def test_codex_login_write_back(tmp_path, monkeypatch):
    import sol_fusion as sf
    user = tmp_path / 'auth.json'
    user.write_text('{"v": 1}')
    monkeypatch.setattr(sf, 'USER_AUTH', user)
    priv = tmp_path / 'p'
    priv.mkdir()
    home, sha = sf.private_codex_home(priv)
    assert sf.return_refreshed_login(home, sha) == 'unchanged'
    (home / 'auth.json').write_text('{"v": 2}')
    assert 'written back' in sf.return_refreshed_login(home, sha) and user.read_text() == '{"v": 2}'
    assert oct(user.stat().st_mode & 0o777) == '0o600'
    user.write_text('{"v": 3}')                      # the user's login changed meanwhile: never overwrite it
    priv2 = tmp_path / 'q'
    priv2.mkdir()
    home2, sha2 = sf.private_codex_home(priv2)
    user.write_text('{"v": 4}')
    (home2 / 'auth.json').write_text('{"v": 5}')
    assert 'left as is' in sf.return_refreshed_login(home2, sha2) and user.read_text() == '{"v": 4}'


def test_codex_turn_that_ignores_interrupt_raises(tmp_path, monkeypatch):
    import sol_fusion as sf
    monkeypatch.setattr(sf, 'RUN_WALL_SEC', 0.05)
    monkeypatch.setattr(sf, 'STOP_WAIT_SEC', 0.05)
    lead = sf.CodexLead(tmp_path, tmp_path, None)
    calls = []

    async def rpc(method, params, timeout=90):
        calls.append(method)
        if method == 'thread/start':
            return {'model': sf.LEAD_MODEL, 'reasoningEffort': sf.LEAD_EFFORT, 'thread': {'id': 't'}}
        if method == 'turn/start':
            return {'turn': {'id': 'u'}}
        if method == 'turn/interrupt':
            raise RuntimeError('already finishing')   # swallowed
        return {}
    lead.rpc = rpc
    with pytest.raises(RuntimeError, match='did not finish after interrupt'):
        asyncio.run(lead.run('ws://x', 'task', '/app'))
    assert calls.count('turn/interrupt') == 1


def test_plan_rotates_arms_and_configs():
    names = launch.planned()
    assert len(names) == 40 and len(set(names)) == 40
    firsts = [names[i] for i in range(0, 40, 5)]
    assert {n.rsplit('-', 0)[0].split('-')[0] + ('-' + n.split('-')[1] if n.startswith('haiku') else '') for n in firsts} == {'opus', 'fusion', 'sol', 'haiku-solo', 'haiku-advisor'}
    assert launch.config('haiku-advisor-hof-topology-interpenetration-r1')['agents'][0]['import_path'] == 'fusion_arms:HaikuAdvisor'
    assert launch.config('haiku-solo-hof-topology-interpenetration-r1')['agents'][0]['import_path'] == 'fusion_arms:HaikuSolo'
    c = launch.config('fusion-wal-recovery-ordering-r1')
    assert c['agents'][0]['import_path'] == 'fusion_arms:Fusion' and c['retry'] == {'max_retries': 0}
    assert c['tasks'][0]['path'].endswith('raw/tasks/wal-recovery-ordering')
    assert launch.config('opus-solo-cad-model-r1')['agents'][0]['import_path'] == 'fusion_arms:OpusSolo'


def test_sol_config_and_codex_pricing():
    import sol_fusion as sf
    c = launch.config('sol-fusion-hof-topology-interpenetration-r1')
    assert c['agents'][0]['import_path'] == 'sol_fusion:SolFusion'
    assert c['tasks'][0]['path'].endswith('raw/tasks/hof-topology-interpenetration')
    u = {'inputTokens': 1_000_000, 'cachedInputTokens': 600_000, 'cacheWriteInputTokens': 100_000, 'outputTokens': 100_000}
    # fresh 300k*$2 + cached 600k*$0.10 + writes 100k*$2.50 + out 100k*$10 (all per 1M), long-context doubled
    assert sf.usage_cost('gpt-6.1-sol', u) == pytest.approx((0.3 * 4 + 0.6 * 0.2 + 0.1 * 5 + 0.1 * 15))
    small = {'inputTokens': 10_000, 'cachedInputTokens': 8_000, 'cacheWriteInputTokens': 0, 'outputTokens': 1_000}
    assert sf.usage_cost('gpt-6.1-sol', small) == pytest.approx(0.002 * 2 + 0.008 * 0.1 + 0.001 * 10)
    ev = [{'method': 'thread/tokenUsage/updated', 'params': {'threadId': 't', 'turnId': 'u', 'tokenUsage': {'last': small, 'total': {'x': 1}}}}] * 2
    assert sf.account_codex(ev, 'gpt-6.1-sol')['responses'] == 1, 'duplicate cumulative totals count once'
    assert sf.CODEX_LINUX.name == 'codex-linux-0.163.0-alpha.10.2.tgz', 'rebuilt by make_runtime.sh, not committed'
