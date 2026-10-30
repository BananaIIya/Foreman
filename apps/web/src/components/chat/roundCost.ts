import type { MessageId, UsageRole, UsageTurn } from "@t3tools/contracts";

import type { MessagesTimelineRow } from "./MessagesTimeline.logic";

export interface RoundCost {
  readonly costUsd: number;
  readonly unpricedTurns: number;
  readonly byRole: ReadonlyArray<{ readonly role: UsageRole; readonly costUsd: number }>;
}

type PricedTurn = Pick<UsageTurn, "recordedAt" | "costUsd" | "role">;

/**
 * Prices each round, a user message and everything recorded before the next one
 * (lead and sidekick turns alike), and keys the total by the round's last settled
 * assistant message, the one whose meta row is shown. Turns recorded before the
 * first loaded user message are left out, since their round is not on screen.
 */
export function deriveRoundCostByMessageId(
  rows: ReadonlyArray<MessagesTimelineRow>,
  turns: ReadonlyArray<PricedTurn>,
): ReadonlyMap<MessageId, RoundCost> {
  const rounds: Array<{ startMs: number; lastMetaMessageId: MessageId | null }> = [];
  for (const row of rows) {
    if (row.kind !== "message" && row.kind !== "assistant-meta") continue;
    if (row.message.role === "user") {
      rounds.push({ startMs: Date.parse(row.message.createdAt), lastMetaMessageId: null });
    } else if (
      rounds.length > 0 &&
      row.message.role === "assistant" &&
      (row.kind === "assistant-meta" || row.showAssistantMeta)
    ) {
      rounds[rounds.length - 1]!.lastMetaMessageId = row.message.id;
    }
  }

  const totals = rounds.map(() => ({
    costUsd: 0,
    unpricedTurns: 0,
    byRole: new Map<UsageRole, number>(),
  }));
  for (const turn of turns) {
    const recordedMs = Date.parse(turn.recordedAt);
    let index = -1;
    for (let candidate = rounds.length - 1; candidate >= 0; candidate -= 1) {
      if (rounds[candidate]!.startMs <= recordedMs) {
        index = candidate;
        break;
      }
    }
    if (index === -1) continue;
    const total = totals[index]!;
    if (turn.costUsd === null) {
      total.unpricedTurns += 1;
      continue;
    }
    total.costUsd += turn.costUsd;
    total.byRole.set(turn.role, (total.byRole.get(turn.role) ?? 0) + turn.costUsd);
  }

  const result = new Map<MessageId, RoundCost>();
  rounds.forEach((round, index) => {
    const total = totals[index]!;
    if (round.lastMetaMessageId === null) return;
    if (total.costUsd === 0 && total.unpricedTurns === 0) return;
    result.set(round.lastMetaMessageId, {
      costUsd: total.costUsd,
      unpricedTurns: total.unpricedTurns,
      byRole: [...total.byRole].map(([role, costUsd]) => ({ role, costUsd })),
    });
  });
  return result;
}

/** Cents-level precision for small rounds, so a $0.03 round does not read as $0.00. */
export function formatRoundCost(costUsd: number): string {
  return costUsd >= 1 ? `$${costUsd.toFixed(2)}` : `$${costUsd.toFixed(4)}`;
}
