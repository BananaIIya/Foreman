import { assert, it } from "@effect/vitest";
import {
  RunId,
  MessageId,
  ComposerContextId,
  ThreadId,
  ProviderDriverKind,
  type AdvisorPipelineState,
  type OrchestrationV2ThreadProjection,
  type OrchestrationMessageContext,
  type ChatAttachment,
} from "@t3tools/contracts";
import * as Effect from "effect/Effect";
import * as FileSystem from "effect/FileSystem";
import * as Layer from "effect/Layer";
import * as Logger from "effect/Logger";
import * as Path from "effect/Path";
import * as ProviderAdapter from "@t3tools/provider-core/server/ProviderAdapter";
import * as IdAllocator from "@t3tools/provider-core/server/IdAllocator";
import * as GitWorkflow from "../git/GitWorkflowService.ts";
import * as ProjectService from "../project/ProjectService.ts";
import * as ProviderAuthService from "../provider/ProviderAuthService.ts";
import * as ContextHandoffService from "./ContextHandoffService.ts";
import * as EventSink from "./EventSink.ts";
import * as ProjectionStore from "./ProjectionStore.ts";
import * as ProviderSessionManager from "./ProviderSessionManager.ts";
import * as ProviderTurnStart from "./ProviderTurnStartService.ts";
import * as RunExecution from "./RunExecutionService.ts";
import * as RuntimePolicy from "./RuntimePolicy.ts";

const threadId = ThreadId.make("thread:context-budget");
const runId = RunId.make("run:current");
const advisorState: AdvisorPipelineState = {
  phase: "executing",
  task: "ORIGINAL_TASK_COMPLETE: fix the regression without changing public behavior.",
  cycle: 1,
  failures: 1,
  currentRunId: runId,
  plan: {
    markdown: "FULL_PLAN_MARKER: change sum.ts and run the sum regression tests.",
    path: "/synthetic/plan.md",
    files: ["sum.ts"],
    commands: [{ command: "node", args: ["--test", "sum.test.mjs"] }],
  },
  summary: "VALIDATION_FEEDBACK: still failing. " + "界".repeat(1_000),
  diff: "+diff evidence 🧪\n".repeat(500),
};

const capturedTask =
  "Fix this error: [terminal selection](t3-context://v1/terminal/captured_error)";
const capturedContext: OrchestrationMessageContext = {
  version: 1,
  records: [
    {
      version: 1,
      contextId: ComposerContextId.make("captured_error"),
      kind: "terminal",
      label: "error",
      terminalId: "terminal",
      terminalLabel: "Terminal",
      lineStart: 1,
      lineEnd: 1,
      text: "CAPTURED_ERROR: expected 42, received 17",
    },
  ],
};
const capturedAttachments: readonly ChatAttachment[] = [
  { type: "image", id: "captured_image", name: "error.png", mimeType: "image/png", sizeBytes: 128 },
];

it.effect.each([undefined, "worker", "advisor"] as const)(
  "preserves captured context on the first %s turn",
  (role) =>
    Effect.gen(function* () {
      const test = harness({
        ...(role === undefined ? {} : { role }),
        text: capturedTask,
        context: capturedContext,
        attachments: capturedAttachments,
        priorCycle: 0,
        pipelineState: { ...advisorState, cycle: 0, plan: null, task: capturedTask },
      });
      yield* test.start;
      assert.include(test.sent[0]!.message.text, "CAPTURED_ERROR: expected 42, received 17");
      assert.deepEqual(test.sent[0]!.message.attachments, capturedAttachments);
    }),
);

it.effect.each(["worker", "advisor"] as const)(
  "reloads original context for a later %s cycle",
  (role) =>
    Effect.gen(function* () {
      const test = harness({
        role,
        priorCycle: 0,
        text: "",
        originalTask: { context: capturedContext, attachments: capturedAttachments },
        pipelineState: {
          ...advisorState,
          task: capturedTask,
          taskMessageId: MessageId.make("original-task"),
        },
      });
      yield* test.start;
      assert.include(test.sent[0]!.message.text, "CAPTURED_ERROR: expected 42, received 17");
      assert.deepEqual(test.sent[0]!.message.attachments, capturedAttachments);
    }),
);

function harness(input: {
  readonly role?: "worker" | "advisor";
  readonly modelWindow?: number;
  readonly nativeUsed?: number;
  readonly text?: string;
  readonly resumeFailure?: boolean;
  readonly priorCycle?: number;
  readonly priorNativeId?: string;
  readonly priorTurnStatus?: "completed" | "failed";
  readonly appContext?: string;
  readonly escapedMaterials?: boolean;
  readonly pipelineState?: AdvisorPipelineState;
  readonly context?: OrchestrationMessageContext;
  readonly attachments?: readonly ChatAttachment[];
  readonly originalTask?: {
    readonly context: OrchestrationMessageContext;
    readonly attachments: readonly ChatAttachment[];
  };
}) {
  const selection = { instanceId: "instance", model: "synthetic" };
  const currentRun = {
    id: runId,
    threadId,
    ordinal: 2,
    providerInstanceId: "instance",
    modelSelection: selection,
    providerThreadId: "provider",
    userMessageId: "message",
    rootNodeId: "root",
    activeAttemptId: "attempt",
    status: "starting",
    startedAt: null,
    ...(input.role === undefined ? {} : { usageRole: input.role, advisorCycle: 1 }),
  };
  let projection = {
    thread: {
      id: threadId,
      projectId: "project",
      branch: null,
      worktreePath: null,
      ...(input.role === undefined
        ? {}
        : {
            advisorState: input.escapedMaterials
              ? { ...advisorState, summary: "\\".repeat(3_000), diff: '"'.repeat(6_000) }
              : (input.pipelineState ?? advisorState),
          }),
    },
    runs: [
      {
        ...currentRun,
        id: "run:previous",
        ordinal: 1,
        rootNodeId: "previous-root",
        activeAttemptId: "previous-attempt",
        status: "completed",
        startedAt: "2026-10-01T00:00:00.000Z",
        ...(input.role === undefined
          ? {}
          : { usageRole: "worker", advisorCycle: input.priorCycle ?? 1 }),
      },
      currentRun,
    ],
    nodes: [{ id: "root", checkpointScopeId: "scope" }],
    attempts: [
      { id: "attempt", runId, providerThreadId: "provider", rootNodeId: "root" },
      {
        id: "previous-attempt",
        runId: "run:previous",
        providerThreadId: "provider",
        rootNodeId: "previous-root",
        nativeThreadId: input.priorNativeId ?? "native",
      },
    ],
    providerThreads: [
      {
        id: "provider",
        providerInstanceId: "instance",
        driver: "codex",
        providerSessionId: "session",
        nativeThreadRef: { nativeId: "native", driver: "codex", strength: "strong" },
        ...(input.nativeUsed === undefined
          ? {}
          : { contextUsage: { usedTokens: input.nativeUsed, maxTokens: input.modelWindow } }),
        handoffIds: [],
      },
    ],
    messages: [
      {
        id: "message",
        text: input.text ?? "Continue",
        attachments: input.attachments ?? [],
        context: input.context,
      },
    ],
    checkpointScopes: [{ id: "scope" }],
    contextHandoffs: [],
    contextTransfers: [],
    providerSessions: [],
    providerTurns: [
      {
        id: "previous-turn",
        providerThreadId: "provider",
        runAttemptId: "previous-attempt",
        nodeId: "previous-root",
        status: input.priorTurnStatus ?? "completed",
        ordinal: 1,
      },
    ],
    subagents: [],
    turnItems: [],
  } as unknown as OrchestrationV2ThreadProjection;
  const sent: ProviderAdapter.ProviderAdapterV2TurnInput[] = [];
  let historyReads = 0;
  let compactions = 0;
  const session = {
    driver: ProviderDriverKind.make("codex"),
    providerSession: {
      id: "session",
      driver: "codex",
    } as ProviderAdapter.ProviderAdapterV2SessionRuntime["providerSession"],
    getModelContextWindow: () => input.modelWindow,
    resumeThread: ({ providerThread }) =>
      input.resumeFailure
        ? Effect.fail(
            new ProviderAdapter.ProviderAdapterResumeThreadError({
              driver: providerThread.driver,
              providerSessionId: providerThread.providerSessionId!,
              providerThreadId: providerThread.id,
            }),
          )
        : Effect.succeed(providerThread),
    ensureThread: ({ existingProviderThread }) =>
      Effect.succeed({
        ...existingProviderThread!,
        nativeThreadRef: {
          nativeId: "replacement",
          driver: ProviderDriverKind.make("codex"),
          strength: "strong" as const,
        },
      }),
    startTurn: (turn) =>
      Effect.sync(() => {
        sent.push(turn);
      }),
    compactThread: () =>
      Effect.sync(() => {
        compactions++;
      }),
  } satisfies Partial<ProviderAdapter.ProviderAdapterV2SessionRuntime>;
  const dependencies = Layer.mergeAll(
    Layer.mock(ContextHandoffService.ContextHandoffServiceV2)({}),
    IdAllocator.layer,
    FileSystem.layerNoop({}),
    Path.layer,
    Layer.mock(GitWorkflow.GitWorkflowService)({}),
    Layer.mock(ProjectService.ProjectService)({}),
    Layer.mock(ProviderAuthService.ProviderAuthService)({
      tryHandlePromptCommand: () => Effect.succeed(false),
    }),
    Layer.mock(ProjectionStore.ProjectionStoreV2)({
      getTurnStartContext: () => Effect.succeed({ ...projection, hasConversation: true }),
      getRuntimeRecoveryProjection: () => Effect.succeed(projection),
      getThreadRecords: () =>
        Effect.succeed({
          ...projection,
          messages: input.originalTask
            ? [
                {
                  ...projection.messages[0]!,
                  id: MessageId.make("original-task"),
                  ...input.originalTask,
                },
              ]
            : projection.messages,
        }),
      getTurnStartHistory: () =>
        Effect.sync(() => {
          historyReads++;
          return [];
        }),
    }),
    Layer.mock(ProviderSessionManager.ProviderSessionManagerV2)({
      open: () =>
        Effect.succeed(session as unknown as ProviderAdapter.ProviderAdapterV2SessionRuntime),
    }),
    Layer.mock(RuntimePolicy.RuntimePolicyV2)({
      resolve: () =>
        Effect.succeed({ cwd: null, interactionMode: "default", runtimeMode: "full-access" }),
    }),
    Layer.mock(RunExecution.RunExecutionServiceV2)({
      startRootRun: (execution) =>
        execution.session
          .startTurn({
            ...execution,
            threadId,
            runId,
            runOrdinal: execution.run.ordinal,
            rootNodeId: execution.rootNode.id,
            ...(input.appContext === undefined
              ? {}
              : { appContext: [{ key: "app", text: input.appContext }] }),
          })
          .pipe(Effect.orDie),
    }),
    Layer.mock(EventSink.EventSinkV2)({
      write: () => Effect.succeed([]),
      writeIfRunCurrent: ({ events }) =>
        Effect.sync(() => {
          const event = events.findLast((candidate) => candidate.type === "run.updated");
          if (event?.type === "run.updated")
            projection = {
              ...projection,
              runs: projection.runs.map((run) =>
                run.id === event.payload.id ? event.payload : run,
              ),
            };
          return { committed: true, storedEvents: [] };
        }),
    }),
  );
  return {
    sent,
    historyReads: () => historyReads,
    compactions: () => compactions,
    start: Effect.gen(function* () {
      yield* (yield* ProviderTurnStart.ProviderTurnStartServiceV2).start({ threadId, runId });
    }).pipe(Effect.provide(ProviderTurnStart.layer.pipe(Layer.provide(dependencies)))),
  };
}

it.effect(
  "preserves an ordinary prompt under pressure and leaves automatic compaction to the runtime",
  () =>
    Effect.gen(function* () {
      const text = "Original request must remain complete. " + "界".repeat(20_000);
      const test = harness({ modelWindow: 32_000, nativeUsed: 31_000, text });
      const logs: unknown[] = [];
      const logger = Logger.make(({ message }) => {
        logs.push(message);
      });
      yield* test.start.pipe(Effect.provide(Logger.layer([logger], { mergeWithExisting: false })));
      assert.equal(test.sent[0]?.message.text, text);
      assert.equal(test.compactions(), 0);
      assert.equal(test.historyReads(), 0);
      const diagnostics = JSON.stringify(logs);
      assert.include(diagnostics, "input_allowance_exceeded");
      assert.notInclude(diagnostics, "Original request must remain complete");
    }),
);

it.effect("does not invent a capacity limit for an unknown model", () =>
  Effect.gen(function* () {
    const text = "Keep this input: " + "x".repeat(200_000);
    const test = harness({ text });
    yield* test.start;
    assert.equal(test.sent[0]?.message.text, text);
    assert.equal(test.historyReads(), 0);
  }),
);

it.effect(
  "resumes a worker with only validation feedback when this native conversation contains the same cycle",
  () =>
    Effect.gen(function* () {
      const test = harness({ role: "worker", modelWindow: 32_000 });
      yield* test.start;
      const text = test.sent[0]!.message.text;
      assert.include(text, "Continue the same advisor plan");
      assert.include(text, "VALIDATION_FEEDBACK");
      assert.notInclude(text, "FULL_PLAN_MARKER");
      assert.notInclude(text, "ORIGINAL_TASK_COMPLETE");
    }),
);

it.effect.each([
  { resumeFailure: true },
  { priorNativeId: "old-native" },
  { priorCycle: 0 },
  { priorTurnStatus: "failed" as const },
])(
  "sends the complete worker task and plan without full-thread history when context cannot be reused: %j",
  (input) =>
    Effect.gen(function* () {
      const test = harness({ ...input, role: "worker", modelWindow: 32_000 });
      yield* test.start;
      const text = test.sent[0]!.message.text;
      assert.include(text, "FULL_PLAN_MARKER");
      assert.include(text, "ORIGINAL_TASK_COMPLETE");
      assert.equal(test.historyReads(), 0);
    }),
);

it.effect.each([false, true])(
  "fits serialized advisor materials to the selected window, including late MCP app context (escaped=%s)",
  (escapedMaterials) =>
    Effect.gen(function* () {
      const appContext = '"'.repeat(500);
      const test = harness({
        role: "advisor",
        modelWindow: 8_000,
        nativeUsed: 0,
        appContext,
        escapedMaterials,
      });
      yield* test.start;
      const text = test.sent[0]!.message.text;
      assert.include(text, advisorState.task!);
      assert.include(text, "[truncated]");
      assert.isAtMost(
        Buffer.byteLength(JSON.stringify([text, appContext, ""].join("\n\n"))) + 4_000,
        8_000,
      );
      assert.equal(test.historyReads(), 0);
    }),
);
