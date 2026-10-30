import unittest
from types import SimpleNamespace
from checkpoint_evidence import owned_sandbox


class FakeClient:
    def __init__(self, rows):
        self.rows = iter(rows)

    async def list(self, query):
        for row in next(self.rows):
            yield row


class OwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_eventual_visibility(self):
        box = SimpleNamespace(gpu=0)
        waits, logs = [], []
        async def sleep(seconds): waits.append(seconds)
        actual = await owned_sandbox(FakeClient([[], [], [box]]), 'trial__env', logs.append, sleep)
        self.assertIs(actual, box)
        self.assertEqual(waits, [1, 1])
        self.assertEqual([r['matches'] for r in logs], [0, 0, 1])

    async def test_ambiguous_or_gpu_fails_without_retry(self):
        async def sleep(seconds): self.fail('Unsafe identity must not retry')
        for rows in [[SimpleNamespace(gpu=1)], [SimpleNamespace(gpu=0)]*2]:
            with self.assertRaises(RuntimeError):
                await owned_sandbox(FakeClient([rows]), 'trial__env', lambda r: None, sleep)

    async def test_missing_is_bounded(self):
        waits = []
        async def sleep(seconds): waits.append(seconds)
        with self.assertRaises(RuntimeError):
            await owned_sandbox(FakeClient([[]]*11), 'trial__env', lambda r: None, sleep)
        self.assertEqual(len(waits), 10)


class InstructionTests(unittest.TestCase):
    def test_actual_framework_instruction(self):
        from harbor.models.task.task import Task
        from checkpoint_replay_adapter import original_instruction, ROOT
        task = 'satb-audio-transcription'
        received = Task(ROOT/'tasks'/task).instruction
        self.assertEqual(original_instruction(task, received),
                         (ROOT/'tasks'/task/'instruction.md').read_text())
        with self.assertRaises(ValueError):
            original_instruction(task, received + '\nInjected change')
