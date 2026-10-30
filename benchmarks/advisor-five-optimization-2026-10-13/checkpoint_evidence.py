"""Wait for newly created sandbox label indexing; retain ownership checks."""
import asyncio
from evidence import Evidence as OriginalEvidence
from daytona import ListSandboxesQuery


async def owned_sandbox(client, session_id, record, sleep=asyncio.sleep):
    for attempt in range(11):
        found = [s async for s in client.list(ListSandboxesQuery(
            labels={'harbor.session_id': session_id}, limit=2))]
        record({'event': 'checkpoint_ownership_lookup', 'session_id': session_id,
                'attempt': attempt, 'matches': len(found)})
        if found:
            if len(found) != 1 or found[0].gpu not in (0, None):
                raise RuntimeError('Ambiguous ownership or unexpected GPU allocation')
            return found[0]
        if attempt < 10:
            await sleep(1)
    raise RuntimeError('Owned sandbox not visible after bounded label-index wait')


class Evidence(OriginalEvidence):
    async def agent_started(self, event):
        s = await owned_sandbox(self.client, event.trial_name + '__env', self.record)
        self.record({'event': 'agent_started', 'task': event.task_name,
                     'trial_name': event.trial_name, 'sandbox_id': s.id,
                     'cpu': s.cpu, 'memory': s.memory, 'gpu': s.gpu})
