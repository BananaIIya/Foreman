import * as Schema from "effect/Schema";
import { RunId, ThreadId } from "./baseSchemas.ts";
import { ModelSelection } from "./modelSelection.ts";

/**
 * What a user picks to turn on lead + sidekick mode. The thread's own model is the lead;
 * the sidekick is a cheaper model the lead hands briefs to through `sidekick_delegate`.
 */
export const SidekickSelection = Schema.Struct({
  modelSelection: ModelSelection,
});
export type SidekickSelection = typeof SidekickSelection.Type;

/** A thread's sidekick: the chosen model plus the child thread that keeps its session. */
export const SidekickConfiguration = Schema.Struct({
  modelSelection: ModelSelection,
  /** Set on the first delegation and reused after it; cleared when the model changes. */
  threadId: Schema.optional(Schema.NullOr(ThreadId)),
});
export type SidekickConfiguration = typeof SidekickConfiguration.Type;

export const SidekickBrief = Schema.String.check(
  Schema.isMinLength(1),
  Schema.isMaxLength(20_000),
).annotate({
  description:
    "Short, self-contained brief (about 200 words): the goal, the exact public interface and output formats, the edge cases most likely to be missed, constraints (which files it may change, what must not change), how to verify, and what to report. No algorithm walkthrough or implementation code.",
});

export const OrchestratorMcpSidekickDelegateInput = Schema.Struct({
  brief: SidekickBrief,
});
export type OrchestratorMcpSidekickDelegateInput = typeof OrchestratorMcpSidekickDelegateInput.Type;

export const OrchestratorMcpSidekickDelegateResult = Schema.Struct({
  sidekickThreadId: ThreadId,
  runId: Schema.NullOr(RunId),
  status: Schema.Literals(["completed", "failed", "interrupted", "timed_out"]),
  /** The sidekick's report followed by facts recorded by T3: commands with exit codes and changed files. */
  report: Schema.String,
});
export type OrchestratorMcpSidekickDelegateResult =
  typeof OrchestratorMcpSidekickDelegateResult.Type;
