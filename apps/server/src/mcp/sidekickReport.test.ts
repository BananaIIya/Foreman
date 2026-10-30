import { assert, describe, it } from "@effect/vitest";
import type { OrchestrationV2TurnItem } from "@t3tools/contracts";

import { sidekickReport } from "./sidekickReport.ts";

const command = (fields: Record<string, unknown>) =>
  ({ type: "command_execution", status: "completed", ...fields }) as OrchestrationV2TurnItem;

const facts = (turnItems: ReadonlyArray<OrchestrationV2TurnItem>) =>
  sidekickReport({ text: "STATUS: done", outcome: "completed", turnItems, waitMinutes: 30 });

describe("sidekickReport", () => {
  it("counts a failure the provider reported without an exit code", () => {
    const report = facts([
      command({ input: "npm test", status: "failed", output: "Exit code 1\n2 failing" }),
      command({ input: "cat slug.mjs", output: "export function slugify() {}" }),
    ]);
    assert.include(report, "commands run: 2 (1 failed)");
    assert.include(report, "  [failed] npm test");
    assert.include(report, "  [exit unknown] cat slug.mjs");
  });

  it("counts failing output on a command that completed", () => {
    const report = facts([command({ input: "ls missing", output: "ls: missing: No such file" })]);
    assert.include(report, "commands run: 1 (1 failed)");
  });

  it("shows a recorded exit code", () => {
    const report = facts([command({ input: "npm test", exitCode: 0, output: "2 passing" })]);
    assert.include(report, "commands run: 1 (0 failed)");
    assert.include(report, "  [exit 0] npm test");
  });
});
