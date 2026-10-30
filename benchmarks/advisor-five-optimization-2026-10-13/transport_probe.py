"""Actual native cross-provider workspace handoff; contains no benchmark puzzle."""
import json
import shutil
import tempfile
from pathlib import Path
from remote_native_codex import RemoteNativeCodex
from hybrid_cursor import install_cursor, cursor_turn
from hybrid_native import Session
from shared_workspace import make_home, executor
from metrics import aggregate_phases


class TransportProbe(RemoteNativeCodex):
    async def install(self, environment):
        await super().install(environment)
        await install_cursor(self, environment)
        await self.exec_as_root(environment, command='mkdir -p /probe')

    async def run(self, instruction, environment, context):
        logs=self.logs_dir/'phases';logs.mkdir(parents=True,exist_ok=True)
        private=Path(tempfile.mkdtemp(prefix='advisor-cross-provider-probe-',dir='/private/tmp'))
        try:
            first=await cursor_turn(self,environment,logs/'worker-discovery',
                'This is an isolated runtime transport check. Work only in /probe. Use one shell command to write /probe/shared.json containing {"value":7,"worker":"cursor-grok"}. Then finish. Do not delegate or call external services.', 'low')
            assert first['status']=='completed'
            async with executor(self,environment,private) as url:
                home=make_home(private,'advisor','gpt-6.1-sol','high')
                client=Session(home,logs/'advisor-0','gpt-6.1-sol','high')
                try:
                    await client.start()
                    advisor=await client.turn(url,
                        'Read /probe/shared.json from the existing remote workspace. In one shell command, set advisor_seen to its current worker field, double its numeric value, add advisor="codex-sol", and write it back. Do not invent its original values. Finish with a short report.',
                        'You are checking native cross-provider file handoff. Work only in /probe; use the bound remote executor. No delegation or external services.', '/probe')
                finally:
                    await client.close()
                assert advisor['status']=='completed'
            last=await cursor_turn(self,environment,logs/'worker-verification',
                'This is an isolated runtime transport check. Read /probe/shared.json. Use one shell command to assert value == 14, advisor_seen == "cursor-grok", and advisor == "codex-sol". Only after all checks pass, write /probe/verified.txt containing handoff-ok. Finish concisely. Work only in /probe; no delegation or external services.', 'low')
            assert last['status']=='completed'
            check=await environment.exec(command="python3 -c 'import json; from pathlib import Path; d=json.loads(Path(\"/probe/shared.json\").read_text()); assert d[\"value\"]==14 and d[\"advisor_seen\"]==\"cursor-grok\" and d[\"advisor\"]==\"codex-sol\"; assert Path(\"/probe/verified.txt\").read_text().strip()==\"handoff-ok\"; print(\"handoff-verified\")'")
            assert check.return_code==0
            record={'status':'passed','scope':'native cross-provider transport, not a target benchmark result','stages':[first['status'],advisor['status'],last['status']],'usage':aggregate_phases(logs),'verification_stdout':check.stdout}
            (self.logs_dir/'transport-probe.json').write_text(json.dumps(record,indent=2)+'\n')
        finally:
            try:
                await environment.upload_dir(self.logs_dir,str(self.environment_logs_dir))
            finally:
                shutil.rmtree(private)

    def populate_context_post_run(self,context):
        totals=aggregate_phases(self.logs_dir/'phases')
        context.cost_usd=totals['cost_usd']
        context.metadata={'scope':'transport preflight','role_cost_usd':totals['role_cost_usd']}
