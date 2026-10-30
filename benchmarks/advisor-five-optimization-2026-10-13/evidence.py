"""Audit trial ownership without modifying the model's task or feedback."""
import json
from datetime import datetime, timezone
from pathlib import Path
from daytona import AsyncDaytona, ListSandboxesQuery
from harbor.models.job.plugin import BaseJobPlugin

ROOT = Path(__file__).resolve().parent


class Evidence(BaseJobPlugin):
    async def on_job_start(self, job):
        assert len(job) == 1 and job.config.n_attempts == 1 and job.config.retry.max_retries == 0
        self.client = AsyncDaytona()
        job.on_agent_started(self.agent_started)
        job.on_trial_ended(self.trial_ended)

    def record(self, data):
        data['at'] = datetime.now(timezone.utc).isoformat()
        with (ROOT / 'raw/evidence-events.jsonl').open('a') as f:
            f.write(json.dumps(data) + '\n')

    async def agent_started(self, event):
        found = [s async for s in self.client.list(ListSandboxesQuery(
            labels={'harbor.session_id': event.trial_name + '__env'}, limit=2))]
        assert len(found) == 1 and found[0].gpu in (0, None)
        s = found[0]
        self.record({'event': 'agent_started', 'task': event.task_name,
                     'trial_name': event.trial_name, 'sandbox_id': s.id,
                     'cpu': s.cpu, 'memory': s.memory, 'gpu': s.gpu})

    async def trial_ended(self, event):
        self.record({'event': 'trial_ended', 'task': event.task_name,
                     'trial_name': event.trial_name,
                     'reward': event.result.verifier_result.rewards if event.result.verifier_result else None,
                     'error': event.result.exception_info.exception_type if event.result.exception_info else None})

    async def on_job_end(self, job_result):
        await self.client.close()
