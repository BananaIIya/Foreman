import { test } from "vite-plus/test";
import * as NodeAssert from "node:assert/strict";
import * as NodeFSP from "node:fs/promises";
import * as NodeOS from "node:os";
import * as NodePath from "node:path";
import { tasks, coldTasks } from "./tasks.mjs";
import { writeFixture, evaluate } from "./evaluate.mjs";

test.each([...tasks, ...coldTasks])(
  "$id: oracle accepts reference and rejects starter",
  async (task) => {
    const workspace = await NodeFSP.mkdtemp(NodePath.join(NodeOS.tmpdir(), "m4-oracle-"));
    try {
      await writeFixture(task, workspace);
      const starter = await evaluate(task, workspace);
      NodeAssert.equal(
        starter.publicTest.passed,
        false,
        task.id + " starter passed visible checks",
      );
      NodeAssert.equal(starter.hiddenTest.passed, false, task.id + " starter passed hidden checks");
      await writeFixture(task, workspace, task.reference);
      const correct = await evaluate(task, workspace);
      NodeAssert.equal(correct.passed, true, JSON.stringify(correct));
      await NodeFSP.writeFile(NodePath.join(workspace, "public.test.mjs"), "// tampered\n");
      NodeAssert.equal(
        (await evaluate(task, workspace)).passed,
        false,
        "Test changes must fail integrity",
      );
    } finally {
      await NodeFSP.rm(workspace, { recursive: true, force: true });
    }
  },
);

test("retry scorer accepts RangeError because the public contract does not require TypeError", async () => {
  const task = tasks.find((task) => task.id === "retry");
  const workspace = await NodeFSP.mkdtemp(NodePath.join(NodeOS.tmpdir(), "m4-erratum-"));
  try {
    await writeFixture(
      task,
      workspace,
      task.reference.replace("new TypeError()", "new RangeError()"),
    );
    NodeAssert.equal((await evaluate(task, workspace)).passed, true);
  } finally {
    await NodeFSP.rm(workspace, { recursive: true, force: true });
  }
});
