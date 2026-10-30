import { test } from "vite-plus/test";
import * as NodeAssert from "node:assert/strict";
import { accountCodexRollout } from "./codex-accounting.mjs";
import { getProtocol } from "./protocol.mjs";

const expected = { nativeId: "test-session", model: "gpt-6-luna", cwd: "/fixture" };
const prices = getProtocol("gpt").prices;
const context = () => [
  { type: "session_meta", payload: { id: expected.nativeId, cwd: expected.cwd } },
  { type: "turn_context", payload: { model: expected.model, effort: "medium" } },
];
const usage = (input, cached, output, reasoning = 0, write = 0) => ({
  input_tokens: input,
  cached_input_tokens: cached,
  output_tokens: output,
  reasoning_output_tokens: reasoning,
  cache_write_input_tokens: write,
});
const event = (last, total = last) => ({
  type: "event_msg",
  payload: { type: "token_count", info: { last_token_usage: last, total_token_usage: total } },
});

test("native audit deduplicates cumulative events without discarding equal consecutive request deltas", () => {
  const first = event(usage(1000, 800, 100, 40, 50));
  const second = event(usage(1000, 800, 100, 40, 50), usage(2000, 1600, 200, 80, 100));
  const result = accountCodexRollout(
    [...context(), first, first, second, second],
    expected,
    prices,
  );
  NodeAssert.equal(result.requests, 2);
  NodeAssert.equal(result.totals.inputTokens, 2000);
  NodeAssert.equal(result.totals.reasoningTokens, 80);
  // Reasoning is already included in output. Cache writes are removed from ordinary input.
  NodeAssert.equal(result.costUsd, (2 * (150 * 0.1 + 800 * 0.01 + 50 * 0.125 + 100 * 0.5)) / 1e6);
});

test("native audit applies long-context prices per request, not to the session total", () => {
  const result = accountCodexRollout(
    [
      ...context(),
      event(usage(200000, 0, 100)),
      event(usage(200000, 0, 100), usage(400000, 0, 200)),
      event(usage(300000, 0, 100), usage(700000, 0, 300)),
    ],
    expected,
    prices,
  );
  NodeAssert.equal(result.maxPromptTokens, 300000);
  NodeAssert.ok(
    Math.abs(result.costUsd - (2 * (200000 * 0.1 + 100 * 0.5) + 300000 * 0.2 + 100 * 0.75) / 1e6) <
      1e-12,
  );
});

test("native audit rejects incomplete counters and changes to the frozen model or service tier", () => {
  NodeAssert.throws(
    () =>
      accountCodexRollout([...context(), event(usage(1, 0, 1), usage(2, 0, 1))], expected, prices),
    /do not reconcile/,
  );
  NodeAssert.throws(() =>
    accountCodexRollout(
      [...context(), { type: "turn_context", payload: { model: "gpt-6-astra", effort: "medium" } }],
      expected,
      prices,
    ),
  );
  NodeAssert.throws(
    () =>
      accountCodexRollout(
        [
          ...context(),
          {
            type: "event_msg",
            payload: {
              type: "thread_settings_applied",
              thread_settings: { service_tier: "priority" },
            },
          },
        ],
        expected,
        prices,
      ),
    /Non-standard/,
  );
  NodeAssert.throws(() =>
    accountCodexRollout([...context(), event(usage(1, 2, 1))], expected, prices),
  );
  NodeAssert.throws(() =>
    accountCodexRollout([...context(), event(usage(1, 0, 1, 2))], expected, prices),
  );
});
