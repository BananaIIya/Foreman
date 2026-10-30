import { describe, expect, it } from "vite-plus/test";
import {
  promptWithStableInstructions,
  stableProviderConfigurationKey,
} from "./promptCachePolicy.ts";

describe("provider prompt cache policy", () => {
  it("retains the same instruction prefix for different user requests", () => {
    const instructions = "<instructions>Keep the project's conventions.</instructions>";
    const first = promptWithStableInstructions("Fix the picker.", instructions);
    const next = promptWithStableInstructions("Fix the menu.", instructions);
    expect(first.startsWith(`${instructions}\n\n`)).toBe(true);
    expect(next.startsWith(`${instructions}\n\n`)).toBe(true);
    expect(first.endsWith("Fix the picker.")).toBe(true);
    expect(next.endsWith("Fix the menu.")).toBe(true);
  });

  it.each(["/compact", "  /model opus", "/skill:review this diff"])(
    "preserves native command position: %s",
    (command) => {
      expect(promptWithStableInstructions(command, "Instructions")).toBe(
        `${command}\n\nInstructions`,
      );
    },
  );

  it("normalizes object order without reordering command arguments", () => {
    const first = {
      model: "model-a",
      server: { command: "node", args: ["entry.js", "--read-only"], headers: { Z: "z", A: "a" } },
    };
    const reordered = {
      server: { headers: { A: "a", Z: "z" }, args: ["entry.js", "--read-only"], command: "node" },
      model: "model-a",
    };
    expect(stableProviderConfigurationKey(first)).toBe(stableProviderConfigurationKey(reordered));
    expect(JSON.parse(stableProviderConfigurationKey(first)).server.args).toEqual([
      "entry.js",
      "--read-only",
    ]);
    expect(
      stableProviderConfigurationKey({
        ...first,
        server: { ...first.server, args: { 0: "entry.js", 1: "--read-only" } },
      }),
    ).not.toBe(stableProviderConfigurationKey(first));
    expect(
      stableProviderConfigurationKey({
        ...first,
        server: { ...first.server, args: ["--read-only", "entry.js"] },
      }),
    ).not.toBe(stableProviderConfigurationKey(first));
    expect(stableProviderConfigurationKey({ ...first, model: "model-b" })).not.toBe(
      stableProviderConfigurationKey(first),
    );
  });
});
