/** Claude /usage includes CLI auxiliary calls recorded in cost-state, beyond main-agent usage. */
import * as Schema from "effect/Schema";
import { UsageTokenTotals } from "@t3tools/contracts";
import {
  parseTimestampMs,
  tokenCount,
  totalTokens,
  type TranscriptUsageFormat,
  type UsageRecord,
} from "@t3tools/provider-core/server/usage";

const State = Schema.Struct({
  assistantAt: Schema.Record(Schema.String, Schema.Number),
  models: Schema.Record(
    Schema.String,
    Schema.Struct({ totals: UsageTokenTotals, cost: Schema.NullOr(Schema.Number) }),
  ),
});
type State = {
  assistantAt: Record<string, number>;
  models: Record<string, { totals: UsageTokenTotals; cost: number | null }>;
};
const object = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};

function parse(value: unknown, state: State): readonly UsageRecord[] {
  const record = object(value);
  if (typeof record.sessionId !== "string") return [];
  if (record.type === "assistant") {
    const timestamp = parseTimestampMs(record.timestamp);
    if (timestamp !== null) state.assistantAt[record.sessionId] = timestamp;
    return [];
  }
  if (record.type !== "cost-state" || typeof record.sessionId !== "string") return [];
  const timestampMs = state.assistantAt[record.sessionId];
  if (timestampMs === undefined) return [];
  const records: UsageRecord[] = [];
  for (const [model, raw] of Object.entries(object(record.modelUsage))) {
    const usage = object(raw);
    const totals = {
      uncachedInputTokens: tokenCount(usage.inputTokens),
      cachedInputTokens: tokenCount(usage.cacheReadInputTokens),
      cacheCreationTokens: tokenCount(usage.cacheCreationInputTokens),
      outputTokens: tokenCount(usage.outputTokens),
      reasoningTokens: Math.min(tokenCount(usage.outputTokens), tokenCount(usage.thinkingTokens)),
    };
    const cost =
      typeof usage.costUSD === "number" && Number.isFinite(usage.costUSD) && usage.costUSD >= 0
        ? usage.costUSD
        : null;
    const key = `${record.sessionId}:${model}`;
    const previous = state.models[key];
    state.models[key] = { totals, cost };
    const delta = Object.fromEntries(
      Object.entries(totals).map(([key, count]) => [
        key,
        Math.max(0, count - (previous?.totals[key as keyof UsageTokenTotals] ?? 0)),
      ]),
    ) as UsageTokenTotals;
    if (totalTokens(delta) === 0) continue;
    records.push({
      provider: "claude",
      sessionId: record.sessionId,
      model,
      timestampMs,
      totals: delta,
      reportedCostUsd:
        cost === null || previous?.cost === null ? null : Math.max(0, cost - (previous?.cost ?? 0)),
      speed: "standard",
      dedupeKey: `claude-cost:${record.sessionId}:${model}:${totalTokens(totals)}`,
    });
  }
  return records;
}

export const claudeSessionAccountingFormat: TranscriptUsageFormat<State> = {
  usageMarkers: ['"cost-state"', '"type":"assistant"', '"type": "assistant"'],
  selectFields: { type: true, timestamp: true, sessionId: true, modelUsage: true },
  state: { initial: () => ({ assistantAt: {}, models: {} }), schema: State },
  parseLine: (line, state) => {
    try {
      return parse(JSON.parse(line), state);
    } catch {
      return [];
    }
  },
  parseProjected: parse,
};
