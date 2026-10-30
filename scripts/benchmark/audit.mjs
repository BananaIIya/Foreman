import * as NodeFSP from "node:fs/promises";
import * as NodePath from "node:path";
import * as NodeOS from "node:os";
import * as NodeAssert from "node:assert/strict";
import * as NodeChildProcess from "node:child_process";
import { T3Rpc } from "./rpc.mjs";
import { accountedCost } from "./protocol.mjs";

// Independent read-only check against only the native sessions created by this benchmark.
// No transcript text or credentials enter the portable evidence.
const [directory, sessionFile, baseUrl = "http://127.0.0.1:13773"] = process.argv.slice(2);
if (!directory || !sessionFile)
  throw Error("Usage: node scripts/benchmark/audit.mjs RESULTS SESSION_FILE [SERVER_URL]");
const names = (await NodeFSP.readdir(directory)).filter((name) => name.endsWith(".result.json"));
await NodeAssert.rejects(NodeFSP.access(NodePath.join(directory, "runner.lock")), {
  code: "ENOENT",
});
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
  const seenSessions = new Set(),
    audits = [],
    changes = [];
  for (const name of names) {
    const row = JSON.parse(await NodeFSP.readFile(NodePath.join(directory, name), "utf8"));
    const git = (args) =>
      NodeChildProcess.execFileSync("git", args, {
        cwd: row.cwd,
        encoding: "utf8",
      }).trim();
    const fixtureCommitUnchanged = git(["rev-parse", "HEAD"]) === row.baseCommit;
    const changedFiles = git(["diff", "--name-only", row.baseCommit, "--"])
      .split("\n")
      .filter(Boolean);
    const newFiles = git(["ls-files", "--others", "--exclude-standard"])
      .split("\n")
      .filter(Boolean);
    if (row.passed) {
      NodeAssert.ok(fixtureCommitUnchanged, "Agent committed changes " + row.id);
      NodeAssert.deepEqual(changedFiles, ["solution.mjs"], "Unexpected changed files " + row.id);
      NodeAssert.deepEqual(newFiles, [], "Unexpected new files " + row.id);
    }
    const projection = JSON.parse(
      await NodeFSP.readFile(NodePath.join(directory, row.id + ".projection.json"), "utf8"),
    );
    const turns = usage.get(row.threadId);
    NodeAssert.ok(turns?.length, "Missing ledger " + row.id);
    const costUsd = accountedCost(turns);
    NodeAssert.notEqual(costUsd, null, "Unknown cost " + row.id);
    const totals = {
      inputTokens: 0,
      cachedInputTokens: 0,
      cacheCreationTokens: 0,
      outputTokens: 0,
    };
    let nativePriceUsd = 0;
    let nativeSessionCostUsd = 0;
    const sessionTotals = Object.fromEntries(Object.keys(totals).map((key) => [key, 0]));
    const sessionModels = {};
    let completeSessionAccounting = true;
    const nativeModels = new Set();
    const sessions = [];
    for (const thread of projection.providerThreads) {
      const id = thread.nativeThreadRef?.nativeId;
      NodeAssert.ok(id && !seenSessions.has(id), "Native conversation reused: " + row.id);
      seenSessions.add(id);
      sessions.push(id);
      const file = NodePath.join(
        process.env.CLAUDE_CONFIG_DIR || NodePath.join(NodeOS.homedir(), ".claude"),
        "projects",
        row.cwd.replace(/[^a-zA-Z0-9]/g, "-"),
        id + ".jsonl",
      );
      const messages = new Map();
      let finalCostState;
      for (const line of (await NodeFSP.readFile(file, "utf8")).split("\n").filter(Boolean)) {
        const item = JSON.parse(line);
        if (item.type === "cost-state") finalCostState = item;
        if (item.type === "assistant" && item.message?.usage && item.message?.id)
          messages.set(item.message.id, item.message);
      }
      if (!finalCostState?.modelUsage) {
        completeSessionAccounting = false;
      } else {
        let sessionCost = 0;
        for (const [model, u] of Object.entries(finalCostState.modelUsage)) {
          NodeAssert.ok(
            Number.isFinite(u.costUSD) && u.costUSD >= 0,
            "Invalid session cost " + row.id,
          );
          const input = u.inputTokens ?? 0,
            cached = u.cacheReadInputTokens ?? 0;
          const creation = u.cacheCreationInputTokens ?? 0,
            output = u.outputTokens ?? 0;
          sessionTotals.inputTokens += input + cached + creation;
          sessionTotals.cachedInputTokens += cached;
          sessionTotals.cacheCreationTokens += creation;
          sessionTotals.outputTokens += output;
          sessionCost += u.costUSD;
          const held = (sessionModels[model] ??= { inputTokens: 0, outputTokens: 0, costUsd: 0 });
          held.inputTokens += input + cached + creation;
          held.outputTokens += output;
          held.costUsd += u.costUSD;
        }
        NodeAssert.ok(
          Math.abs(sessionCost - finalCostState.totalCostUSD) < 1e-8,
          "Native session total mismatch " + row.id,
        );
        nativeSessionCostUsd += sessionCost;
      }
      for (const message of messages.values()) {
        const u = message.usage;
        const input = u.input_tokens ?? 0,
          cached = u.cache_read_input_tokens ?? 0;
        const creation = u.cache_creation_input_tokens ?? 0,
          output = u.output_tokens ?? 0;
        totals.inputTokens += input + cached + creation;
        totals.cachedInputTokens += cached;
        totals.cacheCreationTokens += creation;
        totals.outputTokens += output;
        nativeModels.add(message.model);
        NodeAssert.ok(
          ["claude-opus-5-5", "claude-haiku-5-5"].includes(message.model),
          "Unexpected native model",
        );
        const haiku = message.model === "claude-haiku-5-5";
        const rates = haiku ? [0.1, 0.01, 0.125, 0.2, 0.5] : [4, 0.2, 5, 8, 20];
        const creation1h = u.cache_creation?.ephemeral_1h_input_tokens ?? 0;
        const creation5m = creation - creation1h;
        NodeAssert.ok(creation5m >= 0);
        const multiplier = haiku && input + cached + creation > 100_000 ? 5 : 1;
        nativePriceUsd +=
          (multiplier *
            (input * rates[0] +
              cached * rates[1] +
              creation5m * rates[2] +
              creation1h * rates[3] +
              output * rates[4])) /
          1e6;
      }
    }
    const ledgerTotals = Object.fromEntries(
      Object.keys(totals).map((key) => [key, turns.reduce((sum, t) => sum + t[key], 0)]),
    );
    const scope = [...new Set(turns.map((t) => t.accountingScope))];
    // The final cost-state can arrive only after the CLI exits. Verify every
    // auxiliary token and dollar against that snapshot, not just the main messages.
    let auditedPriceUsd;
    if (scope.every((value) => value === "sessionWithAuxiliary")) {
      NodeAssert.ok(completeSessionAccounting, "Missing final native cost-state " + row.id);
      NodeAssert.deepEqual(ledgerTotals, sessionTotals, "Native session token mismatch " + row.id);
      auditedPriceUsd = nativeSessionCostUsd;
    } else {
      NodeAssert.deepEqual(scope, ["mainAgent"], "Mixed accounting scopes " + row.id);
      NodeAssert.deepEqual(ledgerTotals, totals, "Native token mismatch " + row.id);
      auditedPriceUsd = nativePriceUsd;
    }
    NodeAssert.ok(Math.abs(costUsd - auditedPriceUsd) < 1e-8, "Native price mismatch " + row.id);
    if (row.costUsd !== costUsd || JSON.stringify(row.turns) !== JSON.stringify(turns)) {
      changes.push({
        id: row.id,
        before: row.initialAccounting?.costUsd ?? row.costUsd,
        after: costUsd,
      });
      row.initialAccounting ??= { costUsd: row.costUsd, turns: row.turns };
      row.costUsd = costUsd;
      row.turns = turns;
      await NodeFSP.writeFile(NodePath.join(directory, name), JSON.stringify(row, null, 2) + "\n");
    }
    audits.push({
      id: row.id,
      nativeModels: [...nativeModels],
      nativeSessions: sessions,
      nativeTokenTotals: totals,
      ledgerTokenTotals: ledgerTotals,
      accountingScopes: scope,
      costUsd,
      nativeMainAgentPriceUsd: nativePriceUsd,
      nativeSessionCostUsd: completeSessionAccounting ? nativeSessionCostUsd : null,
      nativeSessionTokenTotals: completeSessionAccounting ? sessionTotals : null,
      nativeSessionModels: sessionModels,
      nativePriceDifferenceUsd: costUsd - auditedPriceUsd,
      originalCostUsd: row.initialAccounting?.costUsd ?? costUsd,
      changedFiles,
      newFiles,
      fixtureCommitUnchanged,
    });
  }
  const output = {
    auditedAt: new Date().toISOString(),
    trials: audits.length,
    nativeSessions: seenSessions.size,
    uniqueNativeSessions: true,
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
      nativeSessions: seenSessions.size,
      changedAccounting: changes.length,
    }),
  );
} finally {
  rpc.close();
}
