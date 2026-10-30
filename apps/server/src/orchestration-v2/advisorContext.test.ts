import * as NodeServices from "@effect/platform-node/NodeServices";
import { assert, it } from "@effect/vitest";
import type { AdvisorPipelineState } from "@t3tools/contracts";
import * as Effect from "effect/Effect";
import * as FileSystem from "effect/FileSystem";
import { advisorContext } from "./advisorContext.ts";
import { advisorPrompt } from "./advisorPlan.ts";

const state: AdvisorPipelineState = {
  phase: "planning",
  task: "Fix src/sum.mjs",
  cycle: 1,
  failures: 1,
  currentRunId: null,
  plan: null,
  summary: "Existing sum.test.mjs fails",
  diff: "",
};

it.effect("supplies bounded file excerpts without reading hidden or oversized files", () =>
  Effect.gen(function* () {
    const fs = yield* FileSystem.FileSystem;
    const cwd = yield* fs.makeTempDirectoryScoped({ prefix: "advisor-context-" });
    yield* fs.makeDirectory(`${cwd}/src`);
    yield* fs.writeFileString(`${cwd}/src/sum.mjs`, "SOURCE_MARKER\n" + "x".repeat(4_000));
    yield* fs.writeFileString(`${cwd}/large.json`, "LARGE_FILE_MARKER" + "x".repeat(128_000));
    yield* fs.makeDirectory(`${cwd}/.private`);
    yield* fs.writeFileString(`${cwd}/.private/secrets.json`, "SECRET_MARKER");
    const context = yield* advisorContext(cwd, {
      ...state,
      task: "Fix src/sum.mjs and large.json. .private/secrets.json",
    });
    assert.include(context, "SOURCE_MARKER");
    assert.include(context, "[truncated]");
    assert.notInclude(context, "LARGE_FILE_MARKER");
    assert.notInclude(context, "SECRET_MARKER");
    assert.isAtMost(context.length, 12_000);
  }).pipe(Effect.scoped, Effect.provide(NodeServices.layer)),
);

it("preserves the whole task while bounding advisor history and workspace evidence", () => {
  const prompt = advisorPrompt(
    {
      ...state,
      task: "x".repeat(23_950) + "IMPORTANT_TASK_END",
      summary: "s".repeat(6_000),
      diff: "d".repeat(12_000),
    },
    "c".repeat(40_000),
  );
  assert.include(prompt, "IMPORTANT_TASK_END");
  assert.notInclude(prompt, "s".repeat(3_001));
  assert.notInclude(prompt, "d".repeat(6_001));
  assert.notInclude(prompt, "c".repeat(12_001));
  assert.isAtMost(prompt.length, 54_000);
});

it("shares the model's byte allowance across evidence without cutting the task or UTF-8", () => {
  const input = {
    ...state,
    task: "完整用户约束".repeat(200),
    summary: "FAILURE_MARKER " + "失败".repeat(2_000),
    diff: "DIFF_MARKER " + "修改".repeat(4_000),
  };
  const context = "WORKSPACE_MARKER " + "源码".repeat(6_000);
  const base = advisorPrompt(input, context, { contextByteBudget: 0 });
  for (const budget of [1, 10, 512, 2_048]) {
    const prompt = advisorPrompt(input, context, { contextByteBudget: budget });
    assert.include(prompt, input.task);
    assert.notInclude(prompt, "\uFFFD");
    assert.isAtMost(Buffer.byteLength(prompt) - Buffer.byteLength(base), budget);
    if (budget >= 512) {
      assert.include(prompt, "WORKSPACE_MARKER");
      assert.include(prompt, "FAILURE_MARKER");
      assert.include(prompt, "DIFF_MARKER");
    }
  }
});

it("keeps emoji intact when the character cap cuts through a surrogate pair", () => {
  const prompt = advisorPrompt(state, "a" + "🧪".repeat(6_000), { contextByteBudget: 60_000 });
  assert.isTrue(prompt.isWellFormed());
  assert.notInclude(Buffer.from(prompt).toString("utf8"), "\uFFFD");
  assert.include(prompt, "[truncated]");
});

it("counts JSON escapes inside the allowance for generated evidence", () => {
  const input = { ...state, summary: '"failure"\n'.repeat(400), diff: "\\patch\t".repeat(400) };
  const evidence = "\\".repeat(12_000);
  const base = advisorPrompt(input, evidence, { contextByteBudget: 0 });
  const prompt = advisorPrompt(input, evidence, { contextByteBudget: 1_000 });
  assert.isAtMost(
    Buffer.byteLength(JSON.stringify(prompt)) - Buffer.byteLength(JSON.stringify(base)),
    1_000,
  );
  assert.include(prompt, "[truncated]");
});

it("matches JSON escape widths for controls, Unicode and isolated surrogates", () => {
  const evidence =
    Array.from({ length: 32 }, (_, code) => String.fromCharCode(code)).join("") +
    '"\\é界🧪\ud800x\udc00';
  const input = { ...state, summary: evidence.repeat(120), diff: evidence.repeat(120) };
  const context = evidence.repeat(120);
  const base = advisorPrompt(input, context, { contextByteBudget: 0 });
  for (const budget of [0, 12, 13, 47, 128, 511, 2048]) {
    const prompt = advisorPrompt(input, context, { contextByteBudget: budget });
    assert.isAtMost(
      Buffer.byteLength(JSON.stringify(prompt)) - Buffer.byteLength(JSON.stringify(base)),
      budget,
    );
  }
});
