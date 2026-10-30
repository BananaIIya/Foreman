import { describe, expect, it } from "vite-plus/test";
import type { UsageTurn } from "@t3tools/contracts";
import { summarizeRole, usageTurnsCsv } from "./usageThread.ts";

const turn: UsageTurn = {
  threadId: "thread-1",
  turnId: "turn-1",
  role: "worker",
  provider: "codex",
  model: "gpt-6-luna",
  recordedAt: "2026-10-12T12:00:00Z",
  inputTokens: 100,
  cachedInputTokens: 80,
  cacheCreationTokens: 0,
  outputTokens: 20,
  reasoningTokens: 5,
  usageStatus: "complete",
  costUsd: 0.1,
  costSource: "modelPriced",
  pricingVersion: "fixture",
};

describe("thread usage export", () => {
  it("groups independent roles, retains unpriced turns, and counts cached input only once", () => {
    const report = summarizeRole(
      [turn, { ...turn, role: "advisor", costUsd: 3 }, { ...turn, costUsd: null }],
      "worker",
    );
    expect(report).toEqual({
      costUsd: 0.1,
      inputTokens: 200,
      cachedInputTokens: 160,
      outputTokens: 40,
      unpricedTurns: 1,
      count: 2,
    });
  });
  it("exports exact token counts with proper quoting, Unicode and formula escaping", () => {
    const csv = usageTurnsCsv([
      { environment: "本机", threadTitle: '=SUM(1,2)\n"task"', turn: { ...turn, costUsd: null } },
    ]);
    expect(csv).toContain('"本机","thread-1","\'=SUM(1,2)\n""task"""');
    expect(csv).toContain('"100","80","0","20","5","complete","mainAgent","","modelPriced"');
    expect(csv.endsWith("\r\n")).toBe(true);
  });
  it("writes costs as fixed decimals, never in exponent notation", () => {
    const cost = (costUsd: number) =>
      usageTurnsCsv([{ environment: "local", threadTitle: "t", turn: { ...turn, costUsd } }])
        .split("\r\n")[1]!
        .split(",")[15];
    expect(cost(1.5e-7)).toBe('"0.00000015"');
    expect(cost(0)).toBe('"0"');
    expect(cost(12.5)).toBe('"12.5"');
    expect(cost(0.1 + 0.2)).toBe('"0.3"');
  });
});
