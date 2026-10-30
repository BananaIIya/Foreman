import { MessageId } from "@t3tools/contracts";
import { describe, expect, it } from "vite-plus/test";

import type { MessagesTimelineRow } from "./MessagesTimeline.logic";
import { deriveRoundCostByMessageId, formatRoundCost } from "./roundCost";

const messageRow = (
  id: string,
  role: "user" | "assistant",
  createdAt: string,
  showAssistantMeta = false,
): MessagesTimelineRow => ({
  kind: "message",
  id,
  createdAt,
  message: {
    id: MessageId.make(id),
    role,
    text: id,
    runId: null,
    streaming: false,
    createdAt,
    updatedAt: createdAt,
  },
  durationStart: createdAt,
  showAssistantMeta,
  showAssistantCopyButton: showAssistantMeta,
  assistantCopyStreaming: false,
});

const rows = [
  messageRow("ask-1", "user", "2026-10-29T10:00:00.000Z"),
  messageRow("brief", "assistant", "2026-10-29T10:01:00.000Z"),
  messageRow("answer-1", "assistant", "2026-10-29T10:05:00.000Z", true),
  messageRow("ask-2", "user", "2026-10-29T10:10:00.000Z"),
  messageRow("answer-2", "assistant", "2026-10-29T10:12:00.000Z", true),
];

describe("deriveRoundCostByMessageId", () => {
  it("sums lead and sidekick turns onto the round's settled answer", () => {
    const costs = deriveRoundCostByMessageId(rows, [
      { recordedAt: "2026-10-29T09:00:00.000Z", costUsd: 5, role: "worker" },
      { recordedAt: "2026-10-29T10:01:00.000Z", costUsd: 0.02, role: "lead" },
      { recordedAt: "2026-10-29T10:04:00.000Z", costUsd: 0.01, role: "sidekick" },
      { recordedAt: "2026-10-29T10:05:00.000Z", costUsd: 0.005, role: "lead" },
      { recordedAt: "2026-10-29T10:11:00.000Z", costUsd: null, role: "worker" },
    ]);

    const first = costs.get(MessageId.make("answer-1"));
    expect(first?.costUsd).toBeCloseTo(0.035);
    expect(first?.unpricedTurns).toBe(0);
    expect(first?.byRole.map(({ role }) => role)).toEqual(["lead", "sidekick"]);
    expect(first?.byRole[0]?.costUsd).toBeCloseTo(0.025);

    expect(costs.get(MessageId.make("answer-2"))).toEqual({
      costUsd: 0,
      unpricedTurns: 1,
      byRole: [],
    });
    expect(costs.has(MessageId.make("brief"))).toBe(false);
  });

  it("shows nothing for a round with no recorded turns", () => {
    expect(deriveRoundCostByMessageId(rows, []).size).toBe(0);
  });
});

describe("formatRoundCost", () => {
  it("keeps small rounds readable", () => {
    expect(formatRoundCost(0.0312)).toBe("$0.0312");
    expect(formatRoundCost(1.234)).toBe("$1.23");
  });
});
