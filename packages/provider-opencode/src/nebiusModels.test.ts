import { assert, describe, it } from "@effect/vitest";

import { nebiusOpenCodeConfiguration } from "./nebiusModels.ts";

describe("nebiusOpenCodeConfiguration", () => {
  const models = nebiusOpenCodeConfiguration().provider.nebius.models as Record<
    string,
    { readonly options?: { readonly temperature?: number } }
  >;

  it("sends Nano a temperature of 0.6 and leaves the other models to Nebius's default", () => {
    assert.deepEqual(models["nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"]?.options, {
      temperature: 0.6,
    });
    assert.isUndefined(models["nvidia/nemotron-3-super-120b-a12b"]?.options);
    assert.isUndefined(models["nvidia/Nemotron-3-Ultra-550b-a55b"]?.options);
  });

  it("keeps the credential an environment reference", () => {
    assert.equal(
      nebiusOpenCodeConfiguration().provider.nebius.options.apiKey,
      "{env:NEBIUS_API_KEY}",
    );
  });
});
