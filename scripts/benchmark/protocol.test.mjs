import { test } from "vite-plus/test";
import * as NodeAssert from "node:assert/strict";
import { schedule, protocol, accountedCost, getProtocol } from "./protocol.mjs";

test("matrix covers every task/group/repetition exactly once and balances ordering", () => {
  const rows = schedule();
  NodeAssert.equal(rows.length, 108);
  NodeAssert.equal(new Set(rows.map((row) => row.id)).size, 108);
  const tasks = new Set(rows.map((row) => row.taskId));
  NodeAssert.equal(tasks.size, 12);
  for (const task of tasks)
    for (const group of ["A", "B", "C"])
      NodeAssert.deepEqual(
        rows
          .filter((row) => row.taskId === task && row.group === group)
          .map((row) => row.repetition),
        [1, 2, 3],
      );
  const leaders = { A: 0, B: 0, C: 0 };
  for (let i = 0; i < rows.length; i += 3) leaders[rows[i].group]++;
  NodeAssert.deepEqual(leaders, { A: 12, B: 12, C: 12 });
  NodeAssert.deepEqual(protocol.groups.A.worker, protocol.groups.B.advisor);
  NodeAssert.deepEqual(protocol.groups.B.worker, protocol.groups.C.worker);
  NodeAssert.ok(
    protocol.stopLaunchingAtUsd + protocol.concurrency * protocol.trialStopAtUsd <
      protocol.budgetUsd,
  );
});

test("incomplete accounting is unknown, never free; complete zero spend remains valid", () => {
  NodeAssert.equal(accountedCost([]), null);
  NodeAssert.equal(accountedCost([{ usageStatus: "unavailable", costUsd: 0 }]), null);
  NodeAssert.equal(accountedCost([{ usageStatus: "complete", costUsd: null }]), null);
  NodeAssert.equal(accountedCost([{ usageStatus: "complete", costUsd: NaN }]), null);
  NodeAssert.equal(accountedCost([{ usageStatus: "complete", costUsd: -1 }]), null);
  NodeAssert.equal(accountedCost([{ usageStatus: "complete", costUsd: 0 }]), 0);
  NodeAssert.equal(
    accountedCost([
      { usageStatus: "complete", costUsd: 0.25 },
      { usageStatus: "complete", costUsd: 0.5 },
    ]),
    0.75,
  );
});

test("GPT profile preserves the task suite while explicitly selecting Astra/Luna at standard medium", () => {
  const gpt = getProtocol("gpt");
  NodeAssert.equal(gpt.suiteSha256, protocol.suiteSha256);
  NodeAssert.equal(gpt.groups.A.worker.model, "gpt-6-astra");
  NodeAssert.equal(gpt.groups.C.worker.model, "gpt-6-luna");
  NodeAssert.equal(gpt.groups.C.worker.instanceId, "codex");
  NodeAssert.deepEqual(gpt.groups.A.worker, gpt.groups.B.advisor);
  NodeAssert.deepEqual(gpt.groups.B.worker, gpt.groups.C.worker);
  NodeAssert.deepEqual(gpt.groups.C.worker.options, [
    { id: "reasoningEffort", value: "medium" },
    { id: "serviceTier", value: "default" },
  ]);
  NodeAssert.equal(getProtocol(), protocol);
  NodeAssert.throws(() => getProtocol("unknown"), /Unknown/);
});
