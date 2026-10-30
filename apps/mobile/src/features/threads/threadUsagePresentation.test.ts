import type { UsageRole, UsageThread, UsageTurn } from "@t3tools/contracts";
import { describe, expect, it } from "vite-plus/test";

import { threadRoleCosts } from "./threadUsagePresentation";

function turn(threadId: string, role: UsageRole, costUsd: number | null): UsageTurn {
  return {
    threadId,
    turnId: `${threadId}-${role}-${String(costUsd)}`,
    role,
    provider: "codex",
    model: "model",
    recordedAt: "2026-10-20T00:00:00.000Z",
    inputTokens: 10,
    cachedInputTokens: 0,
    cacheCreationTokens: 0,
    outputTokens: 5,
    reasoningTokens: 0,
    usageStatus: "complete",
    costUsd,
    costSource: costUsd === null ? "unpriced" : "modelPriced",
    pricingVersion: "1",
  };
}

function thread(threadId: string, turns: UsageTurn[]): UsageThread {
  return { threadId, projectId: "project", title: threadId, turns };
}

describe("threadRoleCosts", () => {
  it("returns null when the summary has no thread section", () => {
    expect(threadRoleCosts(undefined, ["lead"])).toBeNull();
  });

  it("sums the thread and its sidekick child, skipping roles without turns", () => {
    const usage = [
      thread("lead", [turn("lead", "lead", 1.5), turn("lead", "lead", 0.25)]),
      thread("child", [turn("child", "sidekick", 0.004)]),
      thread("other", [turn("other", "worker", 9)]),
    ];
    expect(threadRoleCosts(usage, ["lead", "child"])).toEqual([
      { role: "lead", label: "Lead", cost: "$1.75", turns: 2 },
      { role: "sidekick", label: "Sidekick", cost: "$0.0040", turns: 1 },
    ]);
  });

  it("marks unpriced turns rather than counting them as free", () => {
    const usage = [
      thread("t", [turn("t", "worker", null), turn("t", "advisor", 2), turn("t", "advisor", null)]),
    ];
    expect(threadRoleCosts(usage, ["t"])).toEqual([
      { role: "worker", label: "Worker", cost: "Unpriced", turns: 1 },
      { role: "advisor", label: "Advisor", cost: "$2.00 + unpriced", turns: 2 },
    ]);
  });

  it("is empty when the thread had no turns in the window", () => {
    expect(threadRoleCosts([], ["t"])).toEqual([]);
  });
});
