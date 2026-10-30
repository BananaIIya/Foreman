import document from "./pricing.json" with { type: "json" };
import type { ModelRate, RateTable, TokenRates } from "./usagePricing.ts";

const tokenRates = (price: {
  input: number;
  cachedInput: number;
  cacheCreation: number;
  cacheCreation1h?: number;
  output: number;
}): TokenRates => ({
  inputCostPerToken: price.input / 1_000_000,
  cacheReadCostPerToken: price.cachedInput / 1_000_000,
  cacheCreationCostPerToken: price.cacheCreation / 1_000_000,
  ...(price.cacheCreation1h === undefined
    ? {}
    : {
        cacheCreation1hCostPerToken: price.cacheCreation1h / 1_000_000,
      }),
  outputCostPerToken: price.output / 1_000_000,
});

export const BUNDLED_PRICING_VERSION = document.version;

/** Offline rates for this fork's worker models. User price overrides still take precedence. */
export const bundledRates: RateTable = new Map(
  Object.entries(document.models).map(([model, price]) => [
    model.toLowerCase(),
    {
      ...tokenRates(price),
      fast: null,
      ultrafast: null,
      ...("longContext" in price
        ? {
            longContext: {
              aboveInputTokens: price.longContext.aboveInputTokens,
              rates: tokenRates(price.longContext),
            },
          }
        : {}),
    } satisfies ModelRate,
  ]),
);

/** Keep provider-qualified Nemotron prices from leaking onto another host of the same model. */
export const withBundledRates = (rates: RateTable): RateTable =>
  new Map([
    ...rates,
    ...[...bundledRates].map(
      ([model, rate]) =>
        [
          model,
          {
            ...rate,
            fast: rates.get(model)?.fast ?? null,
            ultrafast: rates.get(model)?.ultrafast ?? null,
          },
        ] as const,
    ),
  ]);
