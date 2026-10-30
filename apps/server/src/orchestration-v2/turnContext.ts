import type {
  OrchestrationV2ProviderThread,
  OrchestrationV2Run,
  OrchestrationV2ThreadProjection,
} from "@t3tools/contracts";

/** A reused row alone does not prove that the worker received this cycle's task. */
export function workerHasNativeContext(input: {
  readonly projection: Pick<OrchestrationV2ThreadProjection, "runs" | "attempts" | "providerTurns">;
  readonly run: OrchestrationV2Run;
  readonly providerThread: OrchestrationV2ProviderThread;
}): boolean {
  const nativeId = input.providerThread.nativeThreadRef?.nativeId;
  if (nativeId === undefined || input.run.advisorCycle === undefined) return false;
  const priorRuns = new Set(
    input.projection.runs
      .filter(
        (run) =>
          run.ordinal < input.run.ordinal &&
          run.status !== "rolled_back" &&
          run.providerThreadId === input.providerThread.id &&
          run.usageRole === "worker" &&
          run.advisorCycle === input.run.advisorCycle,
      )
      .map((run) => run.id),
  );
  return input.projection.providerTurns.some((turn) => {
    if (turn.status !== "completed" || turn.providerThreadId !== input.providerThread.id)
      return false;
    return input.projection.attempts.some(
      (attempt) =>
        attempt.id === turn.runAttemptId &&
        attempt.rootNodeId === turn.nodeId &&
        attempt.providerThreadId === input.providerThread.id &&
        attempt.nativeThreadId === nativeId &&
        priorRuns.has(attempt.runId),
    );
  });
}

/** Keep the cancellation fact even when optional labels no longer fit. */
export function budgetRestartNote(input: {
  readonly note: string;
  readonly cancelledCount: number;
  readonly availableContextBytes: number | null;
}): string {
  if (
    input.note === "" ||
    input.availableContextBytes === null ||
    Buffer.byteLength(JSON.stringify(input.note)) - 2 <= input.availableContextBytes
  )
    return input.note;
  return `Note: the T3 server restarted. ${input.cancelledCount} background tasks were cancelled and will not report back. Details omitted to conserve context; recover them with t3_thread_read.`;
}
