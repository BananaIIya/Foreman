import * as NodeAssert from "node:assert/strict";

const fields = {
  inputTokens: "input_tokens",
  cachedInputTokens: "cached_input_tokens",
  cacheCreationTokens: "cache_write_input_tokens",
  outputTokens: "output_tokens",
  reasoningTokens: "reasoning_output_tokens",
};
const totals = (usage) =>
  Object.fromEntries(
    Object.entries(fields).map(([key, native]) => {
      const value = usage[native] ?? 0;
      NodeAssert.ok(Number.isSafeInteger(value) && value >= 0, "Invalid native token count");
      return [key, value];
    }),
  );

// Independent of T3's parser: cumulative counters identify duplicate events;
// request deltas must reconcile exactly with the final native totals.
export function accountCodexRollout(lines, expected, prices) {
  let model,
    nativeId,
    lastTotal,
    finalTotal,
    costUsd = 0,
    requests = 0,
    maxPromptTokens = 0;
  const sum = totals({});
  for (const line of lines) {
    const item = typeof line === "string" ? JSON.parse(line) : line;
    const p = item.payload;
    if (!p) continue;
    if (item.type === "session_meta") {
      NodeAssert.equal(nativeId, undefined, "Reused or forked native session");
      nativeId = p.id;
      NodeAssert.equal(nativeId, expected.nativeId);
      NodeAssert.equal(p.cwd, expected.cwd);
      NodeAssert.ok(!p.forked_from_id && !p.source?.subagent, "Unexpected native subagent");
    }
    if (item.type === "turn_context") {
      model = p.model;
      NodeAssert.equal(model, expected.model);
      NodeAssert.equal(p.effort, "medium");
    }
    if (p.type === "thread_settings_applied")
      NodeAssert.ok(
        [undefined, null, "default", "standard"].includes(p.thread_settings?.service_tier),
        "Non-standard service tier",
      );
    if (p.type !== "token_count" || !p.info?.total_token_usage) continue;
    const current = totals(p.info.total_token_usage);
    const signature = JSON.stringify(current);
    if (signature === lastTotal) continue;
    lastTotal = signature;
    finalTotal = current;
    NodeAssert.equal(model, expected.model, "Usage precedes model context");
    const u = totals(p.info.last_token_usage);
    const uncached = u.inputTokens - u.cachedInputTokens - u.cacheCreationTokens;
    NodeAssert.ok(uncached >= 0 && u.reasoningTokens <= u.outputTokens);
    const rate = prices.perMillionTokens[model];
    NodeAssert.ok(rate, "Unknown native model price");
    const long = u.inputTokens > prices.longContext.aboveInputTokens;
    const inputMultiplier = long ? prices.longContext.inputMultiplier : 1;
    const outputMultiplier = long ? prices.longContext.outputMultiplier : 1;
    costUsd +=
      (inputMultiplier *
        (uncached * rate.input +
          u.cachedInputTokens * rate.cacheRead +
          u.cacheCreationTokens * rate.cacheWrite) +
        outputMultiplier * u.outputTokens * rate.output) /
      1e6;
    for (const key of Object.keys(sum)) sum[key] += u[key];
    requests++;
    maxPromptTokens = Math.max(maxPromptTokens, u.inputTokens);
  }
  NodeAssert.ok(nativeId && requests > 0, "Missing native usage");
  NodeAssert.deepEqual(sum, finalTotal, "Request deltas do not reconcile with native totals");
  return { nativeId, model, requests, maxPromptTokens, totals: sum, costUsd };
}
