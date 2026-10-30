import { assert, it } from "@effect/vitest";
import {
  CommandId,
  EventId,
  MessageId,
  OrchestrationV2Command,
  type ProviderInteractionMode,
  type RuntimeMode,
  ProviderDriverKind,
  ProviderInstanceId,
  ThreadId,
  TurnItemId,
} from "@t3tools/contracts";
import * as DateTime from "effect/DateTime";
import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import * as TestClock from "effect/testing/TestClock";

import * as SqlitePersistence from "../persistence/Sqlite.ts";
import { CodexProviderCapabilitiesV2 } from "./Adapters/CodexAdapterV2.ts";
import * as EventSink from "./EventSink.ts";
import * as Orchestrator from "./Orchestrator.ts";
import * as ProviderAdapterRegistry from "./ProviderAdapterRegistry.ts";
import * as ProviderReplayHarness from "./testkit/ProviderReplayHarness.ts";
import { limitRecoveryCommand } from "./UsageLimitRecoveryWorker.ts";

const decodeCommand = Schema.decodeUnknownEffect(OrchestrationV2Command);
const adapter = (provider: string) => ({
  instanceId: ProviderInstanceId.make(provider),
  driver: ProviderDriverKind.make(provider),
  getCapabilities: () => Effect.succeed(CodexProviderCapabilitiesV2),
  planSelectionTransition: () => Effect.succeed({ type: "apply_on_next_turn" as const }),
  openSession: () => Effect.die("This decision test must not open a provider"),
});
const layer = ProviderReplayHarness.layerWithRegistry(
  { name: "sidekick" },
  ProviderAdapterRegistry.layerFromAdapters([adapter("claudeAgent"), adapter("codex")]),
  { databaseLayer: SqlitePersistence.layerMemory, runEffectWorker: false },
);

const threadId = ThreadId.make("sidekick:lead");
const childThreadId = ThreadId.make("sidekick:child");
const haiku = { instanceId: ProviderInstanceId.make("claudeAgent"), model: "claude-haiku-5-5" };
const luna = { instanceId: ProviderInstanceId.make("codex"), model: "gpt-6-luna" };

const create = (extra: Record<string, unknown>) =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    return yield* orchestrator.dispatch(
      yield* decodeCommand({
        type: "thread.create",
        commandId: "create",
        threadId,
        projectId: "project",
        title: "Sidekick test",
        modelSelection: { instanceId: "codex", model: "gpt-6.1-sol" },
        runtimeMode: "full-access",
        interactionMode: "default",
        branch: null,
        worktreePath: null,
        createdBy: "user",
        creationSource: "web",
        ...extra,
      }),
    );
  });

const setSidekick = (id: string, sidekick: { modelSelection: typeof haiku } | null) =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    return yield* orchestrator.dispatch({
      type: "thread.sidekick.set",
      commandId: CommandId.make(id),
      threadId,
      sidekick,
    });
  });

const attach = (id: string, modelSelection: typeof haiku) =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    return yield* orchestrator.dispatch({
      type: "sidekick.attach",
      commandId: CommandId.make(id),
      threadId,
      sidekickThreadId: childThreadId,
      modelSelection,
    });
  });

/** The orchestrator's reason for refusing a command. */
const isDispatchError = Schema.is(Orchestrator.OrchestratorDispatchError);
const reason = (error: unknown) => (isDispatchError(error) ? String(error.cause) : String(error));

const sidekickOf = Effect.gen(function* () {
  const orchestrator = yield* Orchestrator.OrchestratorV2;
  return (yield* orchestrator.getThreadProjection(threadId)).thread.sidekick;
});

const startSidekick = (
  modes = {
    runtimeMode: "full-access" as RuntimeMode,
    interactionMode: "default" as ProviderInteractionMode,
  },
  stopChild = true,
) =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    yield* create({
      ...modes,
      sidekick: { modelSelection: haiku },
      worktreePath: "/synthetic/a",
      branch: "a",
    });
    yield* orchestrator.dispatch({
      type: "message.dispatch",
      commandId: CommandId.make("lead-start"),
      threadId,
      messageId: MessageId.make("lead-start"),
      text: "Fix the bug",
      attachments: [],
      createdBy: "user",
      creationSource: "web",
      dispatchMode: { type: "start_immediately" },
    });
    const leadRun = (yield* orchestrator.getThreadProjection(threadId)).runs[0]!;
    const request = {
      type: "delegated_task.request" as const,
      commandId: CommandId.make("child-start"),
      createdBy: "agent" as const,
      creationSource: "mcp" as const,
      parentThreadId: threadId,
      parentRunId: leadRun.id,
      parentNodeId: leadRun.rootNodeId!,
      task: "Fix one file",
      title: "Sidekick",
      modelSelection: haiku,
      usageRole: "sidekick" as const,
      ...modes,
      completionWake: "settled_only" as const,
    };
    yield* orchestrator.dispatch(request);
    const childId = (yield* sidekickOf)!.threadId!;
    if (stopChild) {
      yield* orchestrator.dispatch({
        type: "thread.stop",
        commandId: CommandId.make("child-stop"),
        threadId: childId,
      });
    }
    const followUp = {
      type: "message.dispatch" as const,
      commandId: CommandId.make("next-brief"),
      threadId: childId,
      senderThreadId: threadId,
      messageId: MessageId.make("next-brief"),
      text: "Continue",
      attachments: [],
      modelSelection: haiku,
      usageRole: "sidekick" as const,
      createdBy: "agent" as const,
      creationSource: "server" as const,
      dispatchMode: { type: "start_immediately" as const },
    };
    return { orchestrator, childId, followUp, request };
  });

it.effect.each(["usage_limit", "restart"] as const)(
  "does not automatically resume a sidekick as a worker after %s",
  (recovery) =>
    Effect.gen(function* () {
      const { orchestrator, childId } = yield* startSidekick(undefined, false);
      const sink = yield* EventSink.EventSinkV2;
      const now = yield* DateTime.now;
      const resetAt = DateTime.formatIso(DateTime.add(now, { minutes: 1 }));
      const run = (yield* orchestrator.getThreadProjection(childId)).runs[0]!;
      yield* sink.write({
        events: [
          {
            id: EventId.make("sidekick:recovery-run"),
            type: "run.updated",
            threadId: childId,
            occurredAt: now,
            payload: {
              ...run,
              status: recovery === "usage_limit" ? "failed" : "cancelled",
              startedAt: now,
              completedAt: now,
            },
          },
          {
            id: EventId.make("sidekick:recovery-error"),
            type: "turn-item.updated",
            threadId: childId,
            occurredAt: now,
            payload: {
              id: TurnItemId.make("sidekick:recovery-error"),
              threadId: childId,
              runId: run.id,
              nodeId: run.rootNodeId,
              providerThreadId: null,
              providerTurnId: null,
              nativeItemRef: null,
              parentItemId: null,
              ordinal: 2,
              status: "failed",
              title: "Usage limit reached",
              startedAt: now,
              completedAt: now,
              updatedAt: now,
              type: "error",
              failure: {
                class: "usage_limit",
                message: "Plan limit reached.",
                resetAt,
                code: null,
                retryable: null,
              },
            },
          },
        ],
      });
      let continuation: OrchestrationV2Command;
      if (recovery === "usage_limit") {
        const shell = (yield* orchestrator.getShellSnapshot()).threads.find(
          (thread) => thread.id === childId,
        )!;
        yield* orchestrator.dispatch(
          limitRecoveryCommand(shell, true, DateTime.toEpochMillis(now))!,
        );
        const armed = (yield* orchestrator.getShellSnapshot()).threads.find(
          (thread) => thread.id === childId,
        )!;
        continuation = limitRecoveryCommand(armed, true, Date.parse(resetAt))!;
        assert.isNotNull(continuation);
      } else {
        continuation = {
          type: "message.dispatch",
          commandId: CommandId.make("sidekick:restart"),
          messageId: MessageId.make("sidekick:restart"),
          threadId: childId,
          restartContinuationOfRunId: run.id,
          text: "Continue where you left off.",
          attachments: [],
          dispatchMode: { type: "start_immediately" },
          createdBy: "user",
          creationSource: "server",
        };
      }
      yield* orchestrator.dispatch({
        type: "thread.runtime-mode.set",
        commandId: CommandId.make("sidekick:lead-lower"),
        threadId,
        runtimeMode: "approval-required",
      });
      yield* orchestrator.dispatch({
        type: "thread.metadata.update",
        commandId: CommandId.make("sidekick:lead-move"),
        threadId,
        worktreePath: "/synthetic/b",
      });
      yield* TestClock.adjust("1 minute");
      const before = yield* orchestrator.getThreadProjection(childId);
      yield* orchestrator.dispatch(continuation);
      const after = yield* orchestrator.getThreadProjection(childId);
      assert.deepEqual(after.runs, before.runs);
      assert.deepEqual(after.messages, before.messages);
    }).pipe(Effect.provide(layer)),
);

it.effect.each([
  { type: "thread.runtime-mode.set", runtimeMode: "approval-required" },
  { type: "thread.interaction-mode.set", interactionMode: "plan" },
  { type: "thread.metadata.update", worktreePath: "/synthetic/b" },
  { type: "thread.metadata.update", worktreePath: null },
  { type: "thread.metadata.update", branch: "b" },
] as const)("rechecks the lead after preflight when %j changes", (change) =>
  Effect.gen(function* () {
    const { orchestrator, followUp, childId } = yield* startSidekick();
    yield* orchestrator.dispatch({ ...change, threadId, commandId: CommandId.make("lead-change") });
    const error = yield* Effect.flip(orchestrator.dispatch(followUp));
    assert.include(reason(error), "no longer matches its lead");
    assert.lengthOf((yield* orchestrator.getThreadProjection(childId)).runs, 1);
    if (change.type === "thread.metadata.update") assert.isNull((yield* sidekickOf)!.threadId);
  }).pipe(Effect.provide(layer)),
);

it.effect("keeps compatible sessions after no-op metadata updates and permission increases", () =>
  Effect.gen(function* () {
    const { orchestrator, followUp, childId } = yield* startSidekick({
      runtimeMode: "approval-required",
      interactionMode: "plan",
    });
    yield* orchestrator.dispatch({
      type: "thread.metadata.update",
      commandId: CommandId.make("no-op"),
      threadId,
      branch: "a",
      worktreePath: "/synthetic/a",
    });
    yield* orchestrator.dispatch({
      type: "thread.runtime-mode.set",
      commandId: CommandId.make("raise"),
      threadId,
      runtimeMode: "full-access",
    });
    assert.equal((yield* sidekickOf)!.threadId, childId);
    yield* orchestrator.dispatch(followUp);
    assert.equal(
      (yield* orchestrator.getThreadProjection(childId)).runs.at(-1)!.usageRole,
      "sidekick",
    );
  }).pipe(Effect.provide(layer)),
);

it.effect("refuses a sidekick raised above the lead after preflight", () =>
  Effect.gen(function* () {
    const { orchestrator, followUp, childId } = yield* startSidekick({
      runtimeMode: "approval-required",
      interactionMode: "default",
    });
    yield* orchestrator.dispatch({
      type: "thread.runtime-mode.set",
      commandId: CommandId.make("child-raise"),
      threadId: childId,
      runtimeMode: "full-access",
    });
    assert.include(
      reason(yield* Effect.flip(orchestrator.dispatch(followUp))),
      "no longer matches its lead",
    );
  }).pipe(Effect.provide(layer)),
);

it.effect("refuses a sidekick converted to an advisor pipeline even when stored modes match", () =>
  Effect.gen(function* () {
    const { orchestrator, followUp, childId } = yield* startSidekick({
      runtimeMode: "full-access",
      interactionMode: "plan",
    });
    yield* orchestrator.dispatch({
      type: "thread.advisor.set",
      commandId: CommandId.make("child-advisor"),
      threadId: childId,
      advisor: { modelSelection: luna },
    });
    assert.include(
      reason(yield* Effect.flip(orchestrator.dispatch(followUp))),
      "no longer matches its lead",
    );
  }).pipe(Effect.provide(layer)),
);

it.effect("does not restart a sidekick when Stop overtakes a pending brief", () =>
  Effect.gen(function* () {
    const { orchestrator, followUp, childId } = yield* startSidekick();
    yield* orchestrator.dispatch({
      type: "thread.stop",
      commandId: CommandId.make("lead-stop"),
      threadId,
    });
    assert.include(
      reason(yield* Effect.flip(orchestrator.dispatch(followUp))),
      "lead is no longer active",
    );
    assert.lengthOf((yield* orchestrator.getThreadProjection(childId)).runs, 1);
  }).pipe(Effect.provide(layer)),
);

it.effect("refuses an old brief after the lead removes its sidekick", () =>
  Effect.gen(function* () {
    const { orchestrator, followUp } = yield* startSidekick();
    yield* orchestrator.dispatch({
      type: "thread.stop",
      commandId: CommandId.make("lead-stop"),
      threadId,
    });
    yield* setSidekick("remove-sidekick", null);
    assert.include(
      reason(yield* Effect.flip(orchestrator.dispatch(followUp))),
      "no longer matches its lead",
    );
  }).pipe(Effect.provide(layer)),
);

it.effect("refuses initial delegation using modes captured before the lead was lowered", () =>
  Effect.gen(function* () {
    const { orchestrator, request } = yield* startSidekick();
    yield* orchestrator.dispatch({
      type: "thread.runtime-mode.set",
      commandId: CommandId.make("lower"),
      threadId,
      runtimeMode: "approval-required",
    });
    const error = yield* Effect.flip(
      orchestrator.dispatch({ ...request, commandId: CommandId.make("stale-create") }),
    );
    assert.include(reason(error), "no longer matches its lead");
    assert.lengthOf((yield* orchestrator.getThreadProjection(threadId)).subagents, 1);
  }).pipe(Effect.provide(layer)),
);

it.effect("keeps the sidekick's child thread only while its model stays the same", () =>
  Effect.gen(function* () {
    yield* create({ sidekick: { modelSelection: haiku } });
    assert.deepEqual(yield* sidekickOf, { modelSelection: haiku, threadId: null });

    yield* attach("attach-1", haiku);
    assert.equal((yield* sidekickOf)?.threadId, childThreadId);

    yield* setSidekick("same-model", { modelSelection: haiku });
    assert.equal((yield* sidekickOf)?.threadId, childThreadId, "the same model keeps its memory");

    yield* setSidekick("new-model", { modelSelection: luna });
    assert.deepEqual(yield* sidekickOf, { modelSelection: luna, threadId: null });

    // A first delegation that started before the change finishes late: it must not attach.
    yield* attach("stale-attach", haiku);
    assert.equal((yield* sidekickOf)?.threadId, null);

    yield* setSidekick("off", null);
    assert.equal(yield* sidekickOf, null);
  }).pipe(Effect.provide(layer)),
);

it.effect("records a sidekick thread's runs as its lead and refuses changes mid-turn", () =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    yield* create({ sidekick: { modelSelection: haiku } });
    yield* orchestrator.dispatch({
      type: "message.dispatch",
      commandId: CommandId.make("task"),
      threadId,
      messageId: MessageId.make("task"),
      text: "Fix the WAL recovery ordering.",
      attachments: [],
      createdBy: "user",
      creationSource: "web",
      dispatchMode: { type: "start_immediately" },
    });
    const projection = yield* orchestrator.getThreadProjection(threadId);
    assert.equal(projection.runs[0]?.usageRole, "lead");
    const refused = yield* Effect.flip(setSidekick("mid-turn", null));
    assert.include(reason(refused), "Finish or stop the current turn");
  }).pipe(Effect.provide(layer)),
);

it.effect("gives the sidekick's child thread the sidekick role and no sidekick of its own", () =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    yield* create({ sidekick: { modelSelection: haiku } });
    yield* orchestrator.dispatch({
      type: "message.dispatch",
      commandId: CommandId.make("lead-task"),
      threadId,
      messageId: MessageId.make("lead-task"),
      text: "Fix the WAL recovery ordering.",
      attachments: [],
      createdBy: "user",
      creationSource: "web",
      dispatchMode: { type: "start_immediately" },
    });
    const leadRun = (yield* orchestrator.getThreadProjection(threadId)).runs[0]!;
    const result = yield* orchestrator.dispatch({
      type: "delegated_task.request",
      createdBy: "agent",
      creationSource: "mcp",
      commandId: CommandId.make("sidekick-create"),
      parentThreadId: threadId,
      parentRunId: leadRun.id,
      parentNodeId: leadRun.rootNodeId!,
      task: "Run the tests.",
      title: "Sidekick",
      modelSelection: haiku,
      usageRole: "sidekick",
      runtimeMode: "full-access",
      interactionMode: "default",
      completionWake: "settled_only",
    });
    const child = result.storedEvents.flatMap((stored) =>
      stored.event.type === "subagent.updated" && stored.event.payload.childThreadId !== null
        ? [stored.event.payload.childThreadId]
        : [],
    )[0]!;
    const projection = yield* orchestrator.getThreadProjection(child);
    assert.equal(projection.thread.sidekick, null);
    assert.equal(projection.runs[0]?.usageRole, "sidekick");
    // Attached in the transaction that started it: an interrupted caller leaves no orphan.
    assert.equal((yield* sidekickOf)?.threadId, child);
  }).pipe(Effect.provide(layer)),
);

it.effect("keeps advisor mode and a sidekick apart", () =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    const advisor = { modelSelection: { instanceId: "codex", model: "gpt-6-sol" } };
    const both = yield* Effect.flip(create({ advisor, sidekick: { modelSelection: haiku } }));
    assert.include(reason(both), "advisor mode or a sidekick, not both");

    yield* create({ commandId: "create-advisor", advisor });
    const sidekickOnAdvisor = yield* Effect.flip(
      setSidekick("on-advisor", { modelSelection: haiku }),
    );
    assert.include(reason(sidekickOnAdvisor), "Turn off advisor mode");

    yield* orchestrator.dispatch({
      type: "thread.advisor.set",
      commandId: CommandId.make("advisor-off"),
      threadId,
      advisor: null,
    });
    yield* setSidekick("on", { modelSelection: haiku });
    const advisorOnSidekick = yield* Effect.flip(
      orchestrator.dispatch(
        yield* decodeCommand({
          type: "thread.advisor.set",
          commandId: "advisor-on",
          threadId,
          advisor,
        }),
      ),
    );
    assert.include(reason(advisorOnSidekick), "Turn off the sidekick");
  }).pipe(Effect.provide(layer)),
);
