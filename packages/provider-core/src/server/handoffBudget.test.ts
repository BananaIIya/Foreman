import { describe, expect, it } from "vite-plus/test";
import {
  ProviderThreadId,
  RunId,
  ThreadId,
  TurnItemId,
  type OrchestrationV2HistoricalMessage,
} from "@t3tools/contracts";
import { historyCost, selectHistory } from "./handoffBudget.ts";

const message = (
  id: string,
  role: "user" | "assistant",
  text: string,
): OrchestrationV2HistoricalMessage => ({
  itemId: TurnItemId.make(id),
  role,
  text,
  threadId: ThreadId.make("thread:history"),
  runId: RunId.make("run:source"),
  providerThreadId: ProviderThreadId.make("provider:source"),
  status: "completed",
  kind: role === "user" ? "user_message" : "command_execution",
});

describe("history selection", () => {
  it("recovers useful context around a 20 MiB tool result without replaying or truncating it", () => {
    const original = message("original", "user", "Keep the existing public API.");
    const huge = message("huge", "assistant", "x".repeat(20 * 1024 * 1024));
    const latest = message("latest", "user", "Continue the migration.");
    const answer = message("answer", "assistant", "The focused tests passed.");
    const selected = selectHistory({
      messages: [original, huge, latest, answer],
      coverage: "Retrieve omitted history with t3_thread_read.",
      budget: 16_000,
    });

    expect(selected.messages).toEqual([original, latest, answer]);
    expect(selected.omittedItemIds).toEqual([huge.itemId]);
    expect(selected.omittedItems).toBe(1);
    expect(selected.context).toContain("Selected 3 intact items; omitted 1 items.");
    expect(historyCost(selected.messages, selected.context)).toBeLessThanOrEqual(16_000);
    expect(huge.text.length).toBe(20 * 1024 * 1024);
  });

  it("omits an oversized priority item and retains earlier complete answers", () => {
    const original = message("original", "user", "Keep the existing public API.");
    const answer = message("answer", "assistant", "The focused tests passed.");
    const huge = message("huge", "assistant", "x".repeat(20 * 1024 * 1024));
    const selected = selectHistory({
      messages: [original, answer, huge],
      coverage: "Retrieve omitted history with t3_thread_read.",
      budget: 16_000,
    });

    expect(selected.messages).toEqual([original, answer]);
    expect(selected.omittedItemIds).toEqual([huge.itemId]);
    expect(historyCost(selected.messages, selected.context)).toBeLessThanOrEqual(16_000);
  });

  it.each(["界🧪مرحبا", '\u0000\\"\n\t', "high:\ud800 low:\udfff"])(
    "keeps whole Unicode and escaped messages within their serialized budget: %j",
    (text) => {
      const original = message("original", "user", "Preserve every character.");
      const oversized = message("oversized", "assistant", text.repeat(4_000));
      const recent = message("recent", "assistant", text.repeat(10));
      const selected = selectHistory({
        messages: [original, oversized, recent],
        coverage: "Recover omitted items by ID.",
        budget: 2_000,
      });

      expect(selected.messages).toEqual([original, recent]);
      expect(selected.omittedItemIds).toEqual([oversized.itemId]);
      expect(selected.messages[1]?.text).toBe(text.repeat(10));
      expect(historyCost(selected.messages, selected.context)).toBeLessThanOrEqual(2_000);
    },
  );
});
