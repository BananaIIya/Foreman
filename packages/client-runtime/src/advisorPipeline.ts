import type { AdvisorPipelineState } from "@t3tools/contracts";

import type { EnvironmentThread } from "./state/models.ts";

type AdvisorPipelineView = Pick<AdvisorPipelineState, "phase" | "summary">;

/** Status line for every pipeline phase, shared so web and mobile read the same. */
export const ADVISOR_PHASE_LABEL: Record<AdvisorPipelineState["phase"], string> = {
  planning: "Advisor is planning",
  paused: "Task paused",
  executing: "Worker is executing",
  completed: "Acceptance checks passed",
  blocked: "Pipeline needs attention",
  stopped: "Pipeline stopped",
};

/** Shown on model and mode controls the server refuses to change mid-pipeline. */
export const ADVISOR_PIPELINE_LOCK_HINT = "Stop the advisor pipeline to change the model or mode.";

/**
 * True while the server rejects worker model, provider, runtime and interaction
 * mode changes for the thread (see the advisor guard in the orchestrator).
 */
export function isAdvisorPipelineBusy(
  state: Pick<AdvisorPipelineState, "phase"> | null | undefined,
): boolean {
  return state?.phase === "planning" || state?.phase === "paused" || state?.phase === "executing";
}

/** Selector for a thread detail atom; returns a boolean so readers rerender only on change. */
export function selectAdvisorPipelineBusy(thread: EnvironmentThread | null): boolean {
  return isAdvisorPipelineBusy(thread?.projection.thread.advisorState);
}

/**
 * The pipeline's latest written result, when it describes the current phase:
 * why it is blocked, or the acceptance results that completed it or sent it back to planning.
 */
export function advisorPipelineSummary(
  state: AdvisorPipelineView | null | undefined,
): { readonly label: string; readonly text: string } | null {
  const text = state?.summary.trim();
  if (!state || !text) return null;
  if (state.phase === "blocked") return { label: "Blocked reason", text };
  if (state.phase === "completed" || state.phase === "planning")
    return { label: "Acceptance results", text };
  return null;
}
