"""Observe remote command wall time and preserve the actual result/error."""
import time

async def observe_command(command,run_command):
    start=time.monotonic()
    response=await run_command(command)
    return {'command':command,'exit_code':response.return_code,
            'stdout':(response.stdout or '')[:14000],'stderr':(response.stderr or '')[:4000],
            'output_truncated':getattr(response,'stdout_truncated',False) or getattr(response,'stderr_truncated',False)
                or len(response.stdout or '')>14000 or len(response.stderr or '')>4000,
            'elapsed_wall_seconds':time.monotonic()-start,
            'elapsed_scope':'Controller wait for remote execution including transport; excludes worker model inference.',
            'deadline_exceeded':getattr(response,'deadline_exceeded',None)}
