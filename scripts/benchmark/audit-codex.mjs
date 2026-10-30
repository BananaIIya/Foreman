import * as NodeFSP from "node:fs/promises";
import * as NodePath from "node:path";
import * as NodeOS from "node:os";
import * as NodeAssert from "node:assert/strict";
import * as NodeChildProcess from "node:child_process";
import { T3Rpc } from "./rpc.mjs";
import { accountedCost } from "./protocol.mjs";
import { accountCodexRollout } from "./codex-accounting.mjs";

const [
  directory,
  sessionFile,
  baseUrl = "http://127.0.0.1:13773",
  codexHome = NodePath.join(NodeOS.homedir(), ".codex"),
] = process.argv.slice(2);
if (!directory || !sessionFile)
  throw Error(
    "Usage: node scripts/benchmark/audit-codex.mjs RESULTS SESSION_FILE [SERVER_URL] [CODEX_HOME]",
  );
await NodeAssert.rejects(NodeFSP.access(NodePath.join(directory, "runner.lock")), {
  code: "ENOENT",
});
const read = async (name) =>
  JSON.parse(await NodeFSP.readFile(NodePath.join(directory, name), "utf8"));
const manifest = await read("manifest.json");
NodeAssert.equal(manifest.profile, "gpt");
const session = JSON.parse(await NodeFSP.readFile(sessionFile, "utf8"));
const rpc = await new T3Rpc(baseUrl, session.token).connect();
try {
  const summary = await rpc.rpc("server.getUsageSummary", {
    sinceDay: "2026-10-12",
    untilDay: new Date().toISOString().slice(0, 10),
    timeZone: "UTC",
    awaitRefresh: true,
  });
  const usage = new Map(summary.threadUsage.map((row) => [row.threadId, row.turns]));
  const audits = [],
    seen = new Set(),
    changes = [];
  for (const file of (await NodeFSP.readdir(directory)).filter((name) =>
    name.endsWith(".result.json"),
  )) {
    const row = await read(file),
      projection = await read(row.id + ".projection.json");
    const sessions = [];
    const group = manifest.groups[row.group === "cold" ? "C" : row.group];
    for (const run of projection.runs) {
      const selected = group[run.usageRole];
      NodeAssert.ok(selected, "Unexpected role " + row.id);
      NodeAssert.equal(run.modelSelection.instanceId, selected.instanceId);
      NodeAssert.equal(run.modelSelection.model, selected.model, "Model drift " + row.id);
      // Pipeline workers may omit options and use native defaults. The rollout
      // audit below still requires Medium reasoning and rejects premium tiers.
      for (const option of run.modelSelection.options ?? [])
        NodeAssert.deepEqual(
          option,
          selected.options.find((item) => item.id === option.id),
        );
    }
    for (const thread of projection.providerThreads) {
      const nativeId = thread.nativeThreadRef?.nativeId;
      NodeAssert.ok(nativeId && !seen.has(nativeId), "Reused session " + row.id);
      seen.add(nativeId);
      const files = await Array.fromAsync(
        NodeFSP.glob("**/*" + nativeId + ".jsonl", { cwd: NodePath.join(codexHome, "sessions") }),
      );
      NodeAssert.equal(files.length, 1, "Missing or ambiguous rollout " + row.id);
      const model = row.runs.find((run) => run.providerThreadId === thread.id)?.model;
      NodeAssert.ok(model, "Missing run model " + row.id);
      const lines = (await NodeFSP.readFile(NodePath.join(codexHome, "sessions", files[0]), "utf8"))
        .split("\n")
        .filter(Boolean);
      sessions.push(accountCodexRollout(lines, { nativeId, model, cwd: row.cwd }, manifest.prices));
    }
    const turns = usage.get(row.threadId);
    NodeAssert.ok(
      turns?.length && turns.every((turn) => turn.accountingScope === "mainAgent"),
      "Missing or unexpected ledger scope",
    );
    const costUsd = accountedCost(turns);
    NodeAssert.notEqual(costUsd, null);
    for (const key of Object.keys(sessions[0].totals))
      NodeAssert.equal(
        turns.reduce((sum, t) => sum + t[key], 0),
        sessions.reduce((sum, s) => sum + s.totals[key], 0),
        "Native token mismatch: " + row.id + " " + key,
      );
    const nativePriceUsd = sessions.reduce((sum, s) => sum + s.costUsd, 0);
    NodeAssert.ok(Math.abs(costUsd - nativePriceUsd) < 1e-8, "Native price mismatch " + row.id);
    const git = (args) =>
      NodeChildProcess.execFileSync("git", args, { cwd: row.cwd, encoding: "utf8" }).trim();
    const changedFiles = git(["diff", "--name-only", row.baseCommit, "--"])
      .split("\n")
      .filter(Boolean);
    const newFiles = git(["ls-files", "--others", "--exclude-standard"])
      .split("\n")
      .filter(Boolean);
    const fixtureCommitUnchanged = git(["rev-parse", "HEAD"]) === row.baseCommit;
    if (row.passed) {
      NodeAssert.deepEqual(changedFiles, ["solution.mjs"]);
      NodeAssert.deepEqual(newFiles, []);
      NodeAssert.ok(fixtureCommitUnchanged);
    }
    if (row.costUsd !== costUsd || JSON.stringify(row.turns) !== JSON.stringify(turns)) {
      changes.push({ id: row.id, before: row.costUsd, after: costUsd });
      row.initialAccounting ??= { costUsd: row.costUsd, turns: row.turns };
      row.costUsd = costUsd;
      row.turns = turns;
      await NodeFSP.writeFile(NodePath.join(directory, file), JSON.stringify(row, null, 2) + "\n");
    }
    audits.push({
      id: row.id,
      sessions,
      costUsd,
      nativePriceUsd,
      nativePriceDifferenceUsd: costUsd - nativePriceUsd,
      changedFiles,
      newFiles,
      fixtureCommitUnchanged,
    });
  }
  const output = {
    auditedAt: new Date().toISOString(),
    trials: audits.length,
    nativeSessions: seen.size,
    uniqueNativeSessions: true,
    accountingScope: "mainAgent",
    changes,
    audits,
  };
  await NodeFSP.writeFile(
    NodePath.join(directory, "native-accounting-audit.json"),
    JSON.stringify(output, null, 2) + "\n",
  );
  console.log(
    JSON.stringify({
      trials: audits.length,
      nativeSessions: seen.size,
      changedAccounting: changes.length,
    }),
  );
} finally {
  rpc.close();
}
