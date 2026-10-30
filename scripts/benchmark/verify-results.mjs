import * as NodeFSP from "node:fs/promises";
import * as NodePath from "node:path";
import * as NodeOS from "node:os";
import * as NodeAssert from "node:assert/strict";
import { tasks, coldTasks } from "./tasks.mjs";
import { protocol } from "./protocol.mjs";
import { evaluate, hash, writeFixture } from "./evaluate.mjs";

// Recheck published candidate code without credentials, T3, or model calls.
const directory = process.argv[2];
if (!directory) throw Error("Usage: node scripts/benchmark/verify-results.mjs RESULTS");
const read = async (name) =>
  JSON.parse(await NodeFSP.readFile(NodePath.join(directory, name), "utf8"));
const manifest = await read("manifest.json");
NodeAssert.equal(manifest.suiteSha256, protocol.suiteSha256, "Use the matching evaluator revision");
const records = await read("results.json");
const solutions = await read("solutions.json");
const planned = [
  ...manifest.schedule,
  ...coldTasks.map((task) => ({ id: "cold-" + task.id, taskId: task.id })),
];
NodeAssert.deepEqual(records.map((row) => row.id).sort(), planned.map((row) => row.id).sort());
NodeAssert.deepEqual(Object.keys(solutions).sort(), planned.map((row) => row.id).sort());
const workspace = await NodeFSP.mkdtemp(NodePath.join(NodeOS.tmpdir(), "m4-recheck-"));
let passed = 0;
try {
  for (const row of records) {
    NodeAssert.equal(row.taskId, planned.find((item) => item.id === row.id).taskId);
    NodeAssert.equal(
      hash(solutions[row.id]),
      row.evaluation.solutionSha256,
      row.id + " changed solution",
    );
    const task = [...tasks, ...coldTasks].find((item) => item.id === row.taskId);
    await writeFixture(task, workspace, solutions[row.id]);
    const score = await evaluate(task, workspace);
    NodeAssert.equal(score.passed, row.evaluation.passed, row.id + " changed score");
    if (score.passed) passed++;
  }
} finally {
  await NodeFSP.rm(workspace, { recursive: true, force: true });
}
console.log(JSON.stringify({ verified: records.length, passed, modelCalls: 0 }));
