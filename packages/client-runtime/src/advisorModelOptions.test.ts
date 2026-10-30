import { ProviderDriverKind, ProviderInstanceId, type ServerProvider } from "@t3tools/contracts";
import { describe, expect, it } from "vite-plus/test";

import { advisorModelOptions, sidekickModelOptions } from "./advisorModelOptions.ts";

function provider(instanceId: string, overrides: Partial<ServerProvider> = {}): ServerProvider {
  return {
    instanceId: ProviderInstanceId.make(instanceId),
    driver: ProviderDriverKind.make("codex"),
    enabled: true,
    installed: true,
    version: null,
    status: "ready",
    auth: { status: "authenticated" },
    checkedAt: "2026-10-15T00:00:00.000Z",
    models: [{ slug: "model", name: "Model", isCustom: false, capabilities: {} }],
    slashCommands: [],
    skills: [],
    ...overrides,
  };
}

describe("advisorModelOptions", () => {
  it("offers only available advisor drivers, including older servers without availability", () => {
    const providers = [
      provider("codex", { displayName: "Personal" }),
      provider("claude", { driver: ProviderDriverKind.make("claudeAgent") }),
      provider("disabled", { status: "disabled" }),
      provider("unavailable", { availability: "unavailable" }),
      provider("worker", { driver: ProviderDriverKind.make("opencode") }),
    ];
    const { options, fallback } = advisorModelOptions(providers);
    expect(options.map((option) => option.selection.instanceId)).toEqual(["codex", "claude"]);
    expect(options[0]?.label).toBe("Personal · Model");
    expect(fallback).toBe(options[0]);
  });

  it("retains only the selected instance's legacy model", () => {
    const legacy = provider("selected", {
      models: [
        { slug: "legacy", name: "Legacy", isLegacy: true, isCustom: false, capabilities: {} },
      ],
    });
    const providers = [legacy, { ...legacy, instanceId: ProviderInstanceId.make("other") }];
    expect(advisorModelOptions(providers).options).toEqual([]);
    const { options, fallback } = advisorModelOptions(providers, {
      instanceId: legacy.instanceId,
      model: "legacy",
    });
    expect(options.map((option) => option.selection)).toEqual([
      { instanceId: legacy.instanceId, model: "legacy" },
    ]);
    expect(fallback).toBe(options[0]);
  });

  it("does not restore a selected model from a disabled or unavailable provider", () => {
    for (const overrides of [
      { status: "disabled" as const },
      { availability: "unavailable" as const },
    ]) {
      const source = provider("codex", overrides);
      expect(
        advisorModelOptions([source], { instanceId: source.instanceId, model: "model" }),
      ).toEqual({ options: [], fallback: undefined });
    }
  });

  it("prefers the first available advisor model family and handles an empty catalog", () => {
    const source = provider("codex", {
      models: ["other", "gpt-6-sol", "claude-opus"].map((slug) => ({
        slug,
        name: slug,
        isCustom: false,
        capabilities: {},
      })),
    });
    expect(advisorModelOptions([source]).fallback?.selection.model).toBe("gpt-6-sol");
    expect(advisorModelOptions([])).toEqual({ options: [], fallback: undefined });
  });
});

describe("sidekickModelOptions", () => {
  it("offers every available provider and defaults to a cheap model", () => {
    const providers = [
      provider("codex", {
        models: [
          { slug: "gpt-6.1-sol", name: "Sol", isCustom: false, capabilities: {} },
          { slug: "gpt-6-luna", name: "Luna", isCustom: false, capabilities: {} },
        ],
      }),
      provider("nebius", { driver: ProviderDriverKind.make("opencode") }),
      provider("disabled", { status: "disabled" }),
    ];
    const { options, fallback } = sidekickModelOptions(providers);
    expect(options.map((option) => option.selection)).toEqual([
      { instanceId: "codex", model: "gpt-6.1-sol" },
      { instanceId: "codex", model: "gpt-6-luna" },
      { instanceId: "nebius", model: "model" },
    ]);
    expect(fallback?.selection.model).toBe("gpt-6-luna");
  });
});
