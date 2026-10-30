import { assert, describe, it } from "@effect/vitest";
import { bundledRates, withBundledRates } from "./bundledPricing.ts";
import { lookupRate, priceUsage } from "./usagePricing.ts";

describe("bundled official pricing", () => {
  it("prices every Nemotron model with a provider-qualified key offline", () => {
    for (const model of [
      "nebius/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
      "nebius/nvidia/nemotron-3-super-120b-a12b",
      "nebius/nvidia/Nemotron-3-Ultra-550b-a55b",
    ])
      assert.isNotNull(lookupRate(bundledRates, model));
    assert.isNull(lookupRate(bundledRates, "nvidia/nemotron-3-super-120b-a12b"));
  });
  it("uses the Haiku long-context rate only above 100K input, including cached input", () => {
    const price = (input: number) =>
      priceUsage(bundledRates, {
        model: "claude-haiku-5-5",
        totals: {
          uncachedInputTokens: input - 90000,
          cachedInputTokens: 90000,
          cacheCreationTokens: 0,
          outputTokens: 1000,
          reasoningTokens: 500,
        },
        speed: "standard",
        reportedCostUsd: null,
      }).costUsd;
    assert.approximately(price(100000), 0.0024, 1e-12);
    assert.approximately(price(100001), 0.0120005, 1e-12);
  });
  it("prices an aggregate of many requests at base Haiku rates however large its sum", () => {
    // Twenty 25k-token requests summed, as a Claude cost-state delta reports them.
    const aggregate = {
      model: "claude-haiku-5-5",
      totals: {
        uncachedInputTokens: 500_000,
        cachedInputTokens: 0,
        cacheCreationTokens: 0,
        outputTokens: 2_000,
        reasoningTokens: 0,
      },
      speed: "standard" as const,
      reportedCostUsd: null,
    };
    const base = 500_000 * 0.1e-6 + 2_000 * 0.5e-6;
    assert.approximately(
      priceUsage(bundledRates, aggregate, undefined, "aggregate").costUsd,
      base,
      1e-12,
    );
    assert.approximately(priceUsage(bundledRates, aggregate).costUsd, base * 5, 1e-12);
  });
  it("prices Opus and Haiku one-hour writes offline, including the Haiku long-context tier", () => {
    const price = (model: string, cached: number) =>
      priceUsage(bundledRates, {
        model,
        totals: {
          uncachedInputTokens: 0,
          cachedInputTokens: cached,
          cacheCreationTokens: 10000,
          outputTokens: 0,
          reasoningTokens: 0,
        },
        cacheCreation1hTokens: 4000,
        speed: "standard",
        reportedCostUsd: null,
      }).categoryCostUsd!.cacheWrite;
    assert.approximately(price("claude-opus-5-5", 0), 0.062, 1e-12);
    assert.approximately(price("claude-haiku-5-5", 90000), 0.00155, 1e-12);
    assert.approximately(price("claude-haiku-5-5", 90001), 0.00775, 1e-12);
  });
  it("retains published speed premiums when applying bundled standard prices", () => {
    const standard = bundledRates.get("gpt-6-luna")!;
    const fast = { ...standard, inputCostPerToken: 9 };
    const merged = withBundledRates(
      new Map([["gpt-6-luna", { ...standard, inputCostPerToken: 4, fast }]]),
    );
    assert.equal(merged.get("gpt-6-luna")?.inputCostPerToken, standard.inputCostPerToken);
    assert.deepEqual(merged.get("gpt-6-luna")?.fast, fast);
  });
});
