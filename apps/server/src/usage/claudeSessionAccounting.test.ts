import { assert, describe, it } from "@effect/vitest";
import { claudeSessionAccountingFormat as format } from "./claudeSessionAccounting.ts";

const assistant = (sessionId: string, second: number) => ({
  type: "assistant",
  sessionId,
  timestamp: `2026-10-12T12:00:0${second}Z`,
});
const snapshot = (sessionId: string, input: number, output: number, cost?: number) => ({
  type: "cost-state",
  sessionId,
  modelUsage: {
    "claude-haiku-5-5": {
      inputTokens: input,
      cacheReadInputTokens: 100,
      cacheCreationInputTokens: 20,
      outputTokens: output,
      thinkingTokens: 2,
      ...(cost === undefined ? {} : { costUSD: cost }),
    },
  },
});

describe("Claude /usage session accounting", () => {
  it("converts cumulative snapshots to deltas and ignores repeated /usage snapshots", () => {
    const state = format.state!.initial();
    format.parseProjected(assistant("one", 1), state);
    const first = format.parseProjected(snapshot("one", 10, 5, 0.1), state);
    assert.equal(first[0]?.totals.uncachedInputTokens, 10);
    assert.equal(first[0]?.reportedCostUsd, 0.1);
    assert.deepEqual(format.parseProjected(snapshot("one", 10, 5, 0.1), state), []);
    format.parseProjected(assistant("one", 3), state);
    const next = format.parseProjected(snapshot("one", 30, 12, 0.3), state);
    assert.equal(next[0]?.totals.uncachedInputTokens, 20);
    assert.equal(next[0]?.totals.cachedInputTokens, 0);
    assert.equal(next[0]?.totals.outputTokens, 7);
    assert.approximately(next[0]!.reportedCostUsd!, 0.2, 1e-12);
    assert.equal(next[0]?.timestampMs, Date.parse("2026-10-12T12:00:03Z"));
  });
  it("keeps sessions independent when a transcript contains copied history", () => {
    const state = format.state!.initial();
    format.parseProjected(assistant("one", 1), state);
    format.parseProjected(snapshot("one", 50, 5, 0.1), state);
    format.parseProjected(assistant("two", 2), state);
    const next = format.parseProjected(snapshot("two", 20, 4, 0.2), state);
    assert.equal(next[0]?.totals.uncachedInputTokens, 20);
    assert.equal(next[0]?.timestampMs, Date.parse("2026-10-12T12:00:02Z"));
  });
  it("does not invent free billing when cost is absent or accept untimed snapshots", () => {
    const state = format.state!.initial();
    assert.deepEqual(format.parseProjected(snapshot("one", 10, 5), state), []);
    format.parseProjected(assistant("one", 1), state);
    assert.isNull(format.parseProjected(snapshot("one", 10, 5), state)[0]?.reportedCostUsd);
    assert.deepEqual(format.parseLine("invalid", state), []);
  });
});
