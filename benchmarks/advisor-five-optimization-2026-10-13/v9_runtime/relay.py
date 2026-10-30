"""Data-only SDK command relay; the supplied executor runs in Daytona."""
class CommandRelay:
    def __init__(self,execute,limit=16):
        self.execute=execute;self.limit=limit;self.commands=[]

    async def handle(self,message):
        command=message.get('command')
        if not isinstance(command,str) or not command.strip() or len(command)>60000:
            return {'controller_error':'Invalid bounded command; nothing executed'}
        if len(self.commands)>=self.limit:
            return {'controller_error':'Command limit reached; nothing executed'}
        row={'command':command,'started':True}
        self.commands.append(row)
        # No eval, shell, subprocess, or local filesystem operation on model text.
        output=await self.execute(command)
        row.update(exit_code=output.return_code,stdout=output.stdout or '',stderr=output.stderr or '',
            stdout_truncated=getattr(output,'stdout_truncated',False),stderr_truncated=getattr(output,'stderr_truncated',False))
        return row
