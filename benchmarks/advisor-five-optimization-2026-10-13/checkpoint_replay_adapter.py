"""Bridge Harbor's documented canary stripping to the frozen snapshot validator."""
from harbor.models.task.task import strip_canary
from v32_runtime.replay import CheckpointReplay as FrozenReplay, ROOT


def original_instruction(task, received):
    raw = (ROOT/'tasks'/task/'instruction.md').read_text()
    if received.strip() != strip_canary(raw).strip():
        raise ValueError('Replay instruction differs beyond Harbor canary stripping')
    return raw


class CheckpointReplay(FrozenReplay):
    async def run(self, instruction, environment, context):
        task = environment.environment_dir.parent.name
        raw = original_instruction(task, instruction)
        return await super().run(raw, environment, context)
