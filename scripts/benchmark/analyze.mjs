import * as NodeFSP from "node:fs/promises";
import * as NodePath from "node:path";
import * as NodeURL from "node:url";
import { accountedCost } from "./protocol.mjs";

export function summarize(records, manifest) {
  const ids = new Set();
  const planned = new Map(manifest.schedule.map((row) => [row.id, row]));
  const expectedCold = new Set(manifest.cold.map((id) => "cold-" + id));
  for (const row of records) {
    if ((row.scoringRevision ?? 1) !== (manifest.scoringRevision ?? 1))
      throw Error("Mixed scoring revisions: " + row.id);
    if (ids.has(row.id)) throw Error("Duplicate trial: " + row.id);
    ids.add(row.id);
    if (row.group !== "cold") {
      const expected = planned.get(row.id);
      if (!expected || ["group", "taskId", "repetition"].some((k) => row[k] !== expected[k]))
        throw Error("Unexpected trial: " + row.id);
    } else if (!expectedCold.has(row.id)) {
      throw Error("Unexpected cold start: " + row.id);
    }
    if (accountedCost(row.turns) !== row.costUsd) throw Error("Cost ledger mismatch: " + row.id);
    if (
      row.passed &&
      (row.completion !== "completed" || !row.score.passed || !row.toolCalls || row.stopReason)
    )
      throw Error("Invalid pass: " + row.id);
    if (row.passed && row.group === "B") {
      const roles = new Set(row.runs.map((run) => run.usageRole));
      const workerFirst = manifest.advisorStrategy === "worker-first";
      if (
        (!workerFirst && !roles.has("advisor")) ||
        !roles.has("worker") ||
        row.handoffCount !== 0 ||
        (workerFirst && row.runs[0]?.usageRole !== "worker")
      )
        throw Error("Invalid advisor/worker evidence: " + row.id);
      if (new Set(row.runs.map((run) => run.providerThreadId)).size !== row.runs.length)
        throw Error("Reused pipeline session: " + row.id);
    }
  }
  const matrix = records.filter((r) => r.group !== "cold");
  const cold = records.filter((r) => r.group === "cold");
  const cost = (rows) =>
    rows.every((r) => r.costUsd !== null) ? rows.reduce((sum, r) => sum + r.costUsd, 0) : null;
  const groups = Object.fromEntries(
    ["A", "B", "C"].map((group) => {
      const rows = matrix.filter((r) => r.group === group);
      const passed = rows.filter((r) => r.passed).length;
      const totalCostUsd = cost(rows);
      const durations = rows.map((r) => r.durationMs / 1000).sort((a, b) => a - b);
      const middle = Math.floor(durations.length / 2);
      return [
        group,
        {
          recorded: rows.length,
          expected: manifest.schedule.filter((row) => row.group === group).length,
          passed,
          passRate: rows.length ? passed / rows.length : null,
          totalCostUsd,
          costPerTrialUsd: rows.length && totalCostUsd !== null ? totalCostUsd / rows.length : null,
          medianDurationSeconds: !durations.length
            ? null
            : durations.length % 2
              ? durations[middle]
              : (durations[middle - 1] + durations[middle]) / 2,
          advisorCostUsd: rows.every((r) => r.costUsd !== null)
            ? rows
                .flatMap((r) => r.turns)
                .filter((t) => t.role === "advisor")
                .reduce((sum, t) => sum + t.costUsd, 0)
            : null,
        },
      ];
    }),
  );
  const complete =
    matrix.length === planned.size &&
    [...planned.keys()].every((id) => ids.has(id)) &&
    expectedCold.size === 2 &&
    [...expectedCold].every((id) => ids.has(id)) &&
    cold.every((r) => r.passed) &&
    records.every((r) => r.costUsd !== null);
  return {
    scoringRevision: manifest.scoringRevision ?? 1,
    complete,
    recorded: matrix.length,
    expected: planned.size,
    missing: [...planned.keys()].filter((id) => !ids.has(id)),
    coldStarts: {
      recorded: cold.length,
      passed: cold.filter((r) => r.passed).length,
      costUsd: cost(cold),
    },
    groups,
    matrixCostUsd: cost(matrix),
    totalCostUsd: cost(records),
    withinBudget: cost(records) !== null && cost(records) <= manifest.budgetUsd,
    comparison:
      complete && groups.A.recorded > 0 && groups.B.recorded > 0
        ? {
            bVsACostSavingsPercent:
              groups.A.totalCostUsd > 0
                ? (1 - groups.B.totalCostUsd / groups.A.totalCostUsd) * 100
                : null,
            bVsAPassRateDifferencePoints: (groups.B.passRate - groups.A.passRate) * 100,
          }
        : null,
    perTask: [...new Set(manifest.schedule.map((row) => row.taskId))].map((taskId) => ({
      taskId,
      ...Object.fromEntries(
        ["A", "B", "C"].map((group) => {
          const rows = matrix.filter((r) => r.taskId === taskId && r.group === group);
          return [
            group,
            {
              passed: rows.filter((r) => r.passed).length,
              recorded: rows.length,
              costUsd: cost(rows),
            },
          ];
        }),
      ),
    })),
    limitations: [
      "Small synthetic JavaScript tasks; 3 repetitions of the same task are not independent task samples.",
      "Hidden checks are withheld, but all required behavior is described in TASK.md. Harness instructions prohibit accessing other workspaces; this is not an adversarial sandbox.",
      `B uses ${manifest.advisorStrategy ?? "plan-first"} routing and public-acceptance retries. Results compare complete systems rather than model quality alone.`,
      "Native subscription usage is converted to API-equivalent dollars. This is not an invoice or a measurement of subscription cash cost.",
      manifest.profile === "gpt"
        ? "Codex accounting covers the recorded main-agent token usage. No Claude-style native auxiliary cost-state is available; cross-provider totals therefore have different scopes."
        : "Final Claude cost-state includes CLI auxiliary calls, which can use models other than the selected task model. Main-message prices are reported separately in the native accounting audit.",
      "NVIDIA/Nebius cold starts, costs and pass rates have not been evaluated in this experiment.",
    ],
  };
}

export function csv(records) {
  const columns = [
    "id",
    "group",
    "taskId",
    "repetition",
    "passed",
    "completion",
    "costUsd",
    "durationMs",
    "toolCalls",
    "cycles",
    "stopReason",
  ];
  const encode = (value) => '"' + String(value ?? "").replaceAll('"', '""') + '"';
  return (
    [
      columns.map(encode).join(","),
      ...records.map((row) => columns.map((k) => encode(row[k])).join(",")),
    ].join("\n") + "\n"
  );
}

export async function analyze(directory) {
  const manifest = JSON.parse(
    await NodeFSP.readFile(NodePath.join(directory, "manifest.json"), "utf8"),
  );
  const records = await Promise.all(
    (await NodeFSP.readdir(directory))
      .filter((name) => name.endsWith(".result.json"))
      .map(async (name) =>
        JSON.parse(await NodeFSP.readFile(NodePath.join(directory, name), "utf8")),
      ),
  );
  records.sort((a, b) => a.startedAt.localeCompare(b.startedAt) || a.id.localeCompare(b.id));
  const summary = summarize(records, manifest);
  await NodeFSP.writeFile(
    NodePath.join(directory, "summary.json"),
    JSON.stringify(summary, null, 2) + "\n",
  );
  await NodeFSP.writeFile(NodePath.join(directory, "results.csv"), csv(records));
  // Portable evidence deliberately excludes absolute workspace paths and raw provider transcripts.
  const portable = records.map(
    ({
      id,
      group,
      taskId,
      repetition,
      startedAt,
      completedAt,
      durationMs,
      completion,
      toolCalls,
      passed,
      costUsd,
      turns,
      runs,
      handoffCount,
      cycles,
      stopReason,
      score,
      scoringRevision,
      initialEvaluation,
      initialAccounting,
    }) => ({
      id,
      group,
      taskId,
      repetition,
      startedAt,
      completedAt,
      durationMs,
      completion,
      toolCalls,
      passed,
      costUsd,
      turns,
      runs,
      handoffCount,
      cycles,
      stopReason,
      evaluation: {
        passed: score.passed,
        changed: score.changed,
        integrity: score.integrity,
        solutionSha256: score.solutionSha256,
        publicPassed: score.publicTest.passed,
        hiddenPassed: score.hiddenTest.passed,
      },
      scoringRevision: scoringRevision ?? 1,
      originalPassed: initialEvaluation?.passed,
      originalCostUsd: initialAccounting?.costUsd,
    }),
  );
  await NodeFSP.writeFile(
    NodePath.join(directory, "results.json"),
    JSON.stringify(portable, null, 2) + "\n",
  );
  return summary;
}

if (
  process.argv[1] &&
  import.meta.url === NodeURL.pathToFileURL(NodePath.resolve(process.argv[1])).href
) {
  if (!process.argv[2]) throw Error("Usage: node scripts/benchmark/analyze.mjs /path/to/results");
  console.log(JSON.stringify(await analyze(NodePath.resolve(process.argv[2])), null, 2));
}
