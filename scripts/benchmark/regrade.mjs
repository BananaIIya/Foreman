import * as NodeFSP from "node:fs/promises";
import * as NodePath from "node:path";
import * as NodeAssert from "node:assert/strict";
import { tasks, coldTasks } from "./tasks.mjs";
import { evaluate, hash } from "./evaluate.mjs";
import { protocol, schedule } from "./protocol.mjs";

// Apply the documented evaluator correction uniformly, without any provider calls.
const directory = process.argv[2];
if (!directory) throw Error("Usage: node scripts/benchmark/regrade.mjs RESULTS");
await NodeAssert.rejects(NodeFSP.access(NodePath.join(directory, "runner.lock")), {
  code: "ENOENT",
});
NodeAssert.equal(protocol.scoringRevision, 2, "Load the corrected scorer before regrading");
const read = async (name) =>
  JSON.parse(await NodeFSP.readFile(NodePath.join(directory, name), "utf8"));
const write = async (name, value) => {
  const file = NodePath.join(directory, name);
  await NodeFSP.writeFile(file + ".tmp", JSON.stringify(value, null, 2) + "\n");
  await NodeFSP.rename(file + ".tmp", file);
};
const originalManifest = await read("manifest.json");
NodeAssert.deepEqual(originalManifest.groups, protocol.groups);
NodeAssert.deepEqual(originalManifest.schedule, schedule());
const names = (await NodeFSP.readdir(directory)).filter((name) => name.endsWith(".result.json"));
NodeAssert.equal(names.length, 110, "Regrade the entire completed experiment together");
await NodeFSP.copyFile(
  NodePath.join(directory, "manifest.json"),
  NodePath.join(directory, "manifest.before-scoring-fix.json"),
  NodeFSP.constants.COPYFILE_EXCL,
).catch((error) => {
  if (error.code !== "EEXIST") throw error;
});
const changes = [];
for (const name of names) {
  const row = await read(name);
  const task = [...tasks, ...coldTasks].find((task) => task.id === row.taskId);
  NodeAssert.ok(task);
  // A scoring correction cannot rewrite or relax the public task seen by any model.
  for (const [file, content] of Object.entries(task.publicFiles)) {
    if (file !== "solution.mjs")
      NodeAssert.equal(await NodeFSP.readFile(NodePath.join(row.cwd, file), "utf8"), content);
  }
  const before = hash(JSON.stringify(row.score));
  row.initialEvaluation ??= { passed: row.passed, score: row.score };
  row.score = await evaluate(task, row.cwd);
  row.passed =
    !row.stopReason && row.completion === "completed" && row.toolCalls > 0 && row.score.passed;
  row.scoringRevision = protocol.scoringRevision;
  changes.push({
    id: row.id,
    originalPassed: row.initialEvaluation.passed,
    correctedPassed: row.passed,
    originalScoreSha256: hash(JSON.stringify(row.initialEvaluation.score)),
    precedingScoreSha256: before,
    correctedScoreSha256: hash(JSON.stringify(row.score)),
  });
  await write(name, row);
}
await write("scoring-correction.json", {
  correctedAt: new Date().toISOString(),
  revision: protocol.scoringRevision,
  reason: protocol.scoringErrata,
  modelsRerun: 0,
  originalManifestSha256: hash(JSON.stringify(await read("manifest.before-scoring-fix.json"))),
  changedOutcomes: changes.filter((row) => row.originalPassed !== row.correctedPassed).length,
  changes,
});
await write("manifest.json", {
  ...protocol,
  schedule: schedule(),
  cold: coldTasks.map((task) => task.id),
});
console.log(
  JSON.stringify({
    regraded: changes.length,
    changedOutcomes: changes.filter((row) => row.originalPassed !== row.correctedPassed).length,
    modelsRerun: 0,
  }),
);
