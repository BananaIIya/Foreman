import * as Schema from "effect/Schema";
import { TrimmedNonEmptyString } from "./baseSchemas.ts";
import { MessageId, RunId } from "./baseSchemas.ts";
import { ModelSelection } from "./modelSelection.ts";

export const AdvisorConfiguration = Schema.Struct({
  modelSelection: ModelSelection,
  /** @deprecated Accepted from older clients; plans now hand off automatically. */
  pauseAfterPlan: Schema.optional(Schema.Boolean),
  /** Omitted configurations also use worker-first routing. */
  strategy: Schema.optional(Schema.Literals(["worker-first", "plan-first"])),
});
export type AdvisorConfiguration = typeof AdvisorConfiguration.Type;

export const AdvisorAcceptanceCommand = Schema.Struct({
  command: TrimmedNonEmptyString.check(Schema.isMaxLength(512)),
  args: Schema.Array(Schema.String.check(Schema.isMaxLength(4_096))).check(Schema.isMaxLength(32)),
});
export const AdvisorPlan = Schema.Struct({
  markdown: TrimmedNonEmptyString.check(Schema.isMaxLength(24_000)),
  files: Schema.Array(TrimmedNonEmptyString).check(Schema.isMinLength(1), Schema.isMaxLength(40)),
  commands: Schema.Array(AdvisorAcceptanceCommand).check(
    Schema.isMinLength(1),
    Schema.isMaxLength(8),
  ),
  path: TrimmedNonEmptyString,
});
export type AdvisorPlan = typeof AdvisorPlan.Type;

export const AdvisorPipelineState = Schema.Struct({
  // "paused" remains readable for historical tasks; new plans never enter it.
  phase: Schema.Literals(["planning", "paused", "executing", "completed", "blocked", "stopped"]),
  /**
   * Server-only: the task text (it repeats the user's message) and the latest
   * worker diff feed the next prompt. The server strips both from the wire
   * (WireProjection), so clients never receive them; persisted events that
   * still carry them decode unchanged.
   */
  task: Schema.optional(Schema.String.check(Schema.isMaxLength(24_000))),
  /** Server-only: read the original message's context and attachments on every cycle. */
  taskMessageId: Schema.optional(MessageId),
  cycle: Schema.Int.check(Schema.isGreaterThanOrEqualTo(0)),
  failures: Schema.Int.check(Schema.isGreaterThanOrEqualTo(0), Schema.isLessThanOrEqualTo(3)),
  currentRunId: Schema.NullOr(RunId),
  plan: Schema.NullOr(AdvisorPlan),
  summary: Schema.String.check(Schema.isMaxLength(6_000)),
  /** Server-only; see `task`. */
  diff: Schema.optional(Schema.String.check(Schema.isMaxLength(12_000))),
});
export type AdvisorPipelineState = typeof AdvisorPipelineState.Type;

export const AdvisorAdvanceResult = Schema.Union([
  Schema.Struct({ type: Schema.Literal("plan"), plan: AdvisorPlan }),
  Schema.Struct({
    type: Schema.Literal("worker_result"),
    passed: Schema.Boolean,
    summary: Schema.String.check(Schema.isMaxLength(6_000)),
    diff: Schema.String.check(Schema.isMaxLength(12_000)),
  }),
  Schema.Struct({
    type: Schema.Literal("blocked"),
    summary: Schema.String.check(Schema.isMaxLength(6_000)),
  }),
  Schema.Struct({ type: Schema.Literal("stopped") }),
]);
export type AdvisorAdvanceResult = typeof AdvisorAdvanceResult.Type;
