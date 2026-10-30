import { test } from "vite-plus/test";
import * as NodeAssert from "node:assert/strict";
import { summarize, csv } from "./analyze.mjs";
import { schedule, protocol } from "./protocol.mjs";

const result = (row) => ({
  ...row,
  scoringRevision: protocol.scoringRevision ?? 1,
  passed: true,
  completion: "completed",
  costUsd: 1,
  turns: [{ usageStatus: "complete", costUsd: 1, role: "worker" }],
  durationMs: 1000,
  score: { passed: true },
  toolCalls: 1,
  handoffCount: 0,
  runs:
    row.group === "B"
      ? [
          { usageRole: "advisor", providerThreadId: row.id + "-advisor" },
          { usageRole: "worker", providerThreadId: row.id + "-worker" },
        ]
      : [{ usageRole: "worker", providerThreadId: row.id }],
});
const manifest = { ...protocol, schedule: schedule(), cold: ["cold-add", "cold-dedupe"] };

test("worker-first subset accepts direct completion without inventing a control comparison", () => {
  const selected = schedule().filter((row) => row.group === "B");
  const current = { ...manifest, advisorStrategy: "worker-first", schedule: selected };
  const rows = selected.map(result);
  for (const row of rows) row.runs = [{ usageRole: "worker", providerThreadId: row.id }];
  rows.push(
    result({ id: "cold-cold-add", group: "cold" }),
    result({ id: "cold-cold-dedupe", group: "cold" }),
  );
  const summary = summarize(rows, current);
  NodeAssert.equal(summary.complete, true);
  NodeAssert.equal(summary.expected, 36);
  NodeAssert.equal(summary.groups.A.expected, 0);
  NodeAssert.equal(summary.groups.B.passed, 36);
  NodeAssert.equal(summary.groups.B.advisorCostUsd, 0);
  NodeAssert.equal(summary.comparison, null);
  rows[0].runs.push(
    { usageRole: "advisor", providerThreadId: "advice" },
    { usageRole: "worker", providerThreadId: "retry" },
  );
  NodeAssert.equal(summarize(rows, current).complete, true);
  rows[0].runs.shift();
  NodeAssert.throws(() => summarize(rows, current), /Invalid advisor/);
  NodeAssert.throws(
    () => summarize(rows.slice(1), { ...current, advisorStrategy: "plan-first" }),
    /Invalid advisor/,
  );
});

test("partial, duplicate, unpriced and invalid passes cannot appear as completed evidence", () => {
  const row = result(schedule()[0]);
  NodeAssert.equal(summarize([row], manifest).complete, false);
  NodeAssert.equal(summarize([row], manifest).comparison, null);
  NodeAssert.throws(() => summarize([row, row], manifest), /Duplicate/);
  NodeAssert.throws(() => summarize([{ ...row, toolCalls: 0 }], manifest), /Invalid pass/);
  NodeAssert.throws(() => summarize([{ ...row, costUsd: 0 }], manifest), /ledger mismatch/);
  const unknown = { ...row, costUsd: null, turns: [] };
  NodeAssert.equal(summarize([unknown], manifest).totalCostUsd, null);
  NodeAssert.equal(summarize([unknown], manifest).withinBudget, false);
});

test("complete comparison includes failures and cold costs; CSV preserves quoted values", () => {
  const rows = schedule().map(result);
  rows.push(
    result({ id: "cold-cold-add", group: "cold" }),
    result({ id: "cold-cold-dedupe", group: "cold" }),
  );
  for (const row of rows.filter((r) => r.group === "B")) {
    row.costUsd = 0.5;
    row.turns[0].costUsd = 0.5;
  }
  rows.find((r) => r.group === "B").passed = false;
  rows
    .filter((r) => r.group === "A")
    .forEach((row, i) => {
      row.durationMs = (i + 1) * 1000;
    });
  const summary = summarize(rows, manifest);
  NodeAssert.equal(summary.complete, true);
  NodeAssert.equal(summary.groups.A.medianDurationSeconds, 18.5);
  NodeAssert.equal(summary.groups.B.passed, 35);
  NodeAssert.equal(summary.comparison.bVsACostSavingsPercent, 50);
  NodeAssert.ok(Math.abs(summary.comparison.bVsAPassRateDifferencePoints + 100 / 36) < 1e-10);
  NodeAssert.equal(summary.totalCostUsd, 92);
  NodeAssert.equal(summary.withinBudget, false);
  NodeAssert.match(csv([{ id: 'has,"quotes"' }]), /"has,""quotes"""/);
});
