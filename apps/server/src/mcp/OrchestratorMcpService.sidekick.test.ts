import * as NodeServices from "@effect/platform-node/NodeServices";
import { assert, describe, it } from "@effect/vitest";
import {
  EnvironmentId,
  MessageId,
  NodeId,
  type OrchestrationV2AppThread,
  type OrchestrationV2ThreadProjection,
  ProjectId,
  ProviderDriverKind,
  ProviderInstanceId,
  RunId,
  type ServerProvider,
  ThreadId,
} from "@t3tools/contracts";
import type * as ProviderAdapter from "@t3tools/provider-core/server/ProviderAdapter";
import * as DateTime from "effect/DateTime";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Ref from "effect/Ref";
import { McpSchema, McpServer } from "effect/ai";

import * as ProviderAdapterRegistry from "../orchestration-v2/ProviderAdapterRegistry.ts";
import * as ThreadManagementService from "../orchestration-v2/ThreadManagementService.ts";
import * as ProjectService from "../project/ProjectService.ts";
import * as ProviderRegistry from "../provider/ProviderRegistry.ts";
import * as ScheduledTaskService from "../scheduledTasks/ScheduledTaskService.ts";
import * as SecretRequests from "../secrets/SecretRequests.ts";
import * as ServerSettings from "../serverSettings.ts";
import type { McpInvocationScope } from "./McpInvocationContext.ts";
import * as OrchestratorMcpService from "./OrchestratorMcpService.ts";
import * as McpHttpServer from "./McpHttpServer.ts";
import * as McpInvocationContext from "./McpInvocationContext.ts";
import * as ThreadMetadataMcpService from "./ThreadMetadataMcpService.ts";
import { OrchestratorToolkit } from "./toolkits/orchestrator/tools.ts";
import * as handlers from "./toolkits/orchestrator/handlers.ts";

const leadThreadId = ThreadId.make("thread:sidekick-lead");
const sidekickThreadId = ThreadId.make("thread:sidekick-child");
const leadRunId = RunId.make("run:sidekick-lead");
const sidekickRunId = RunId.make("run:sidekick-child");
const leadMessageId = MessageId.make("message:sidekick-lead-task");
const projectId = ProjectId.make("project:sidekick");
const codex = ProviderInstanceId.make("codex");
const claude = ProviderInstanceId.make("claude");
const haiku = { instanceId: claude, model: "claude-haiku-5-5" };
const now = DateTime.makeUnsafe("2026-10-20T10:00:00Z");

const scope: McpInvocationScope = {
  environmentId: EnvironmentId.make("environment:sidekick"),
  requestNamespace: "provider-session:sidekick",
  thread: {
    threadId: leadThreadId,
    providerSessionId: "provider-session:sidekick",
    providerInstanceId: codex,
  },
  client: undefined,
  capabilities: new Set(["orchestration"]),
  issuedAt: 1,
};

const provider = (
  instanceId: ProviderInstanceId,
  driver: string,
  model: string,
): ServerProvider => ({
  instanceId,
  driver: ProviderDriverKind.make(driver),
  enabled: true,
  installed: true,
  version: "test",
  status: "ready",
  auth: { status: "authenticated" },
  checkedAt: "2026-10-20T00:00:00.000Z",
  models: [{ slug: model, name: model, isCustom: false, capabilities: null }],
  slashCommands: [],
  skills: [],
});

const leadProjection = (sidekickChild: ThreadId | null) =>
  ({
    thread: {
      id: leadThreadId,
      projectId,
      title: "Lead",
      createdBy: "user",
      creationSource: "web",
      modelSelection: { instanceId: codex, model: "gpt-6.1-sol" },
      sidekick: { modelSelection: haiku, threadId: sidekickChild },
      runtimeMode: "full-access",
      interactionMode: "default",
      deletedAt: null,
      archivedAt: null,
      branch: null,
      worktreePath: null,
    },
    runs: [
      {
        id: leadRunId,
        ordinal: 1,
        status: "running",
        rootNodeId: NodeId.make("node:sidekick-lead-root"),
        providerInstanceId: codex,
        userMessageId: leadMessageId,
      },
    ],
    messages: [{ id: leadMessageId, text: "Fix the WAL recovery ordering." }],
    contextTransfers: [],
    subagents: [],
  }) as unknown as OrchestrationV2ThreadProjection;

/** The sidekick's finished run: its report plus the work T3 recorded. */
const finishedSidekick = {
  runs: [{ id: sidekickRunId, status: "completed" }],
  messages: [
    {
      id: MessageId.make("message:sidekick-report"),
      runId: sidekickRunId,
      role: "assistant",
      text: "STATUS: done\nDONE: ran the suite",
      updatedAt: now,
    },
  ],
  turnItems: [
    { type: "command_execution", runId: sidekickRunId, input: "pytest -q", exitCode: 1 },
    { type: "command_execution", runId: sidekickRunId, input: "pytest -q tests/wal", exitCode: 0 },
    { type: "file_change", runId: sidekickRunId, fileName: "wal.py" },
  ],
};

interface Harness {
  readonly lead: OrchestrationV2ThreadProjection;
  readonly timedOut?: boolean;
  readonly child?: Partial<OrchestrationV2AppThread>;
}

const run = <A, E, R>(
  harness: Harness,
  body: (
    service: OrchestratorMcpService.OrchestratorMcpServiceShape,
    calls: Ref.Ref<ReadonlyArray<{ readonly method: string; readonly input: any }>>,
  ) => Effect.Effect<A, E, R>,
) =>
  Effect.gen(function* () {
    const calls = yield* Ref.make<ReadonlyArray<{ readonly method: string; readonly input: any }>>(
      [],
    );
    const record = (method: string, input: unknown) =>
      Ref.update(calls, (all) => [...all, { method, input }]);
    // The orchestrator attaches a new sidekick in the request's own transaction.
    let lead = harness.lead;
    const childThread = {
      ...leadProjection(null).thread,
      id: sidekickThreadId,
      modelSelection: haiku,
      sidekick: null,
      forkedFrom: { type: "node" as const, nodeId: NodeId.make("node:sidekick-spawn") },
      lineage: {
        parentThreadId: leadThreadId,
        relationshipToParent: "subagent" as const,
        rootThreadId: leadThreadId,
      },
      ...harness.child,
    };
    const dependencies = Layer.mergeAll(
      NodeServices.layer,
      Layer.mock(ThreadMetadataMcpService.ThreadMetadataMcpService)({}),
      Layer.mock(ThreadManagementService.ThreadManagementService)({
        hasReceipt: () => Effect.succeed(false),
        assertProviderInstanceEnabled: () => Effect.void,
        isProviderInstanceEnabledForProject: () => Effect.succeed(true),
        getThreadShell: (threadId) =>
          Effect.succeed({
            ...(threadId === leadThreadId ? lead.thread : childThread),
            activeRunId: leadRunId,
            providerInstanceId: codex,
          } as never),
        getThreadRecords: (threadId, fields) =>
          Effect.succeed(
            (threadId === leadThreadId
              ? lead
              : (fields as ReadonlyArray<string>).includes("turnItems")
                ? finishedSidekick
                : { thread: childThread, runs: [] }) as never,
          ),
        dispatch: (command) =>
          record("dispatch", command).pipe(
            Effect.tap(() =>
              Effect.sync(() => {
                if (command.type === "delegated_task.request")
                  lead = {
                    ...lead,
                    thread: {
                      ...lead.thread,
                      sidekick: { modelSelection: haiku, threadId: sidekickThreadId },
                    },
                  };
              }),
            ),
            Effect.as({
              sequence: 1,
              storedEvents:
                command.type === "delegated_task.request"
                  ? [
                      {
                        sequence: 1,
                        commandId: null,
                        event: {
                          type: "subagent.updated",
                          threadId: leadThreadId,
                          payload: { origin: "app_owned", childThreadId: sidekickThreadId },
                        },
                      },
                      {
                        sequence: 2,
                        commandId: null,
                        event: {
                          type: "run.created",
                          threadId: sidekickThreadId,
                          payload: { id: sidekickRunId },
                        },
                      },
                    ]
                  : [],
            } as never),
          ),
        sendToThread: (input) =>
          record("send", input).pipe(Effect.as({ run: { id: sidekickRunId } } as never)),
        waitForThread: (input) =>
          record("wait", input).pipe(
            Effect.as({
              threadId: input.threadId,
              run: { id: sidekickRunId, status: harness.timedOut ? "running" : "completed" },
              timedOut: harness.timedOut === true,
            } as never),
          ),
        interruptThread: (input) =>
          record("interrupt", input).pipe(Effect.as({ type: "no_active_run" } as never)),
      }),
      ServerSettings.layerTest(),
      Layer.mock(ProviderRegistry.ProviderRegistry)({
        getProviders: Effect.succeed([
          provider(codex, "codex", "gpt-6.1-sol"),
          provider(claude, "claudeAgent", "claude-haiku-5-5"),
        ]),
        refreshInstance: () => Effect.succeed([]),
      }),
      Layer.succeed(
        ProviderAdapterRegistry.ProviderAdapterRegistryV2,
        ProviderAdapterRegistry.ProviderAdapterRegistryV2.of({
          list: () => Effect.succeed([codex, claude]),
          get: (instanceId) =>
            Effect.succeed({
              instanceId,
            } as unknown as ProviderAdapter.ProviderAdapterV2["Service"]),
        }),
      ),
      Layer.mock(ProjectService.ProjectService)({}),
      Layer.mock(SecretRequests.SecretRequests)({}),
      Layer.mock(ScheduledTaskService.ScheduledTaskService)({}),
    );
    return yield* Effect.gen(function* () {
      const service = yield* OrchestratorMcpService.OrchestratorMcpService;
      return yield* body(service, calls);
    }).pipe(Effect.provide(OrchestratorMcpService.layer.pipe(Layer.provideMerge(dependencies))));
  });

describe("OrchestratorMcpService.delegateToSidekick", () => {
  const client = McpSchema.McpServerClient.of({
    clientId: 1,
    protocolVersion: "2025-06-18",
    clientCapabilities: {},
    clientInfo: { name: "sidekick-test", version: "1" },
    initializePayload: {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "sidekick-test", version: "1" },
    },
    getClient: Effect.die("unused"),
  });
  it.effect.each([
    { runtimeMode: "approval-required", interactionMode: "default" },
    { runtimeMode: "full-access", interactionMode: "plan" },
  ] as const)("the MCP entry replaces an old sidekick above current caller modes %j", (modes) =>
    run(
      {
        lead: {
          ...leadProjection(sidekickThreadId),
          thread: { ...leadProjection(sidekickThreadId).thread, ...modes },
        },
      },
      (_service, calls) =>
        Effect.gen(function* () {
          const result = yield* McpServer.McpServer.pipe(
            Effect.flatMap((server) =>
              server.callTool({
                name: "sidekick_delegate",
                arguments: { brief: "Implement the fix" },
              }),
            ),
            Effect.provideService(McpInvocationContext.McpInvocationContext, scope),
            Effect.provideService(McpSchema.McpServerClient, client),
            Effect.provide(
              McpHttpServer.toolkitRegistration(OrchestratorToolkit, handlers.layer).pipe(
                Layer.provideMerge(McpServer.McpServer.layer),
              ),
            ),
          );
          assert.isNotTrue(result.isError, JSON.stringify(result.content));
          const recorded = yield* Ref.get(calls);
          assert.isFalse(recorded.some((call) => call.method === "send"));
          const request = recorded.find((call) => call.method === "dispatch")!.input;
          assert.equal(request.runtimeMode, modes.runtimeMode);
          assert.equal(request.interactionMode, modes.interactionMode);
        }),
    ),
  );

  it.effect.each([
    { worktreePath: "/old/worktree" },
    { branch: "other" },
    {
      lineage: {
        parentThreadId: ThreadId.make("other-lead"),
        relationshipToParent: "subagent",
        rootThreadId: leadThreadId,
      },
    },
    { advisor: { modelSelection: haiku } },
  ] satisfies Partial<OrchestrationV2AppThread>[])(
    "rebuilds an incompatible saved sidekick %j",
    (child) =>
      run({ lead: leadProjection(sidekickThreadId), child }, (service, calls) =>
        Effect.gen(function* () {
          yield* service.delegateToSidekick(scope, { brief: "Use the current workspace." });
          const recorded = yield* Ref.get(calls);
          assert.isFalse(recorded.some((call) => call.method === "send"));
          assert.equal(recorded.filter((call) => call.method === "dispatch").length, 1);
        }),
      ),
  );
  it.effect("creates the sidekick thread on the first brief and attaches it to the lead", () =>
    run({ lead: leadProjection(null) }, (service, calls) =>
      Effect.gen(function* () {
        const result = yield* service.delegateToSidekick(scope, { brief: "Run the WAL tests." });
        const recorded = yield* Ref.get(calls);
        const dispatched = recorded
          .filter((call) => call.method === "dispatch")
          .map((call) => call.input);
        assert.deepEqual(
          dispatched.map((command) => command.type),
          ["delegated_task.request"],
        );
        const request = dispatched[0];
        assert.equal(request.usageRole, "sidekick");
        assert.deepEqual(request.modelSelection, haiku);
        assert.equal(request.completionWake, "settled_only");
        assert.include(request.task, "Fix the WAL recovery ordering.");
        assert.include(request.task, "BRIEF FROM THE LEAD:\nRun the WAL tests.");
        assert.include(request.task, "STATUS: done | partial | blocked");
        assert.equal(result.status, "completed");
        assert.equal(result.sidekickThreadId, sidekickThreadId);
        assert.include(result.report, "STATUS: done\nDONE: ran the suite");
        assert.include(result.report, "commands run: 2 (1 failed)");
        assert.include(result.report, "[exit 1] pytest -q");
        assert.include(result.report, "files changed: 1\n  wal.py");
      }),
    ),
  );

  it.effect("creates one sidekick when two first briefs race", () =>
    run({ lead: leadProjection(null) }, (service, calls) =>
      Effect.gen(function* () {
        yield* Effect.all(
          [
            service.delegateToSidekick(scope, { brief: "Run the WAL tests." }),
            service.delegateToSidekick(scope, { brief: "Read the WAL module." }),
          ],
          { concurrency: "unbounded" },
        );
        const recorded = yield* Ref.get(calls);
        assert.lengthOf(
          recorded.filter(
            (call) => call.method === "dispatch" && call.input.type === "delegated_task.request",
          ),
          1,
        );
        const send = recorded.find((call) => call.method === "send");
        assert.equal(send?.input.threadId, sidekickThreadId);
      }),
    ),
  );

  it.effect("sends later briefs to the same sidekick thread without an MCP follow-up", () =>
    run({ lead: leadProjection(sidekickThreadId) }, (service, calls) =>
      Effect.gen(function* () {
        const result = yield* service.delegateToSidekick(scope, { brief: "Now fix test 37." });
        const recorded = yield* Ref.get(calls);
        assert.isFalse(recorded.some((call) => call.method === "dispatch"));
        const send = recorded.find((call) => call.method === "send")!.input;
        assert.equal(send.threadId, sidekickThreadId);
        assert.equal(send.createdBy, "agent");
        // A server send skips the follow-up record that would also wake the lead.
        assert.equal(send.creationSource, "server");
        assert.equal(send.usageRole, "sidekick");
        assert.deepEqual(send.modelSelection, haiku);
        assert.isTrue(send.text.startsWith("BRIEF FROM THE LEAD:\nNow fix test 37."));
        assert.notInclude(send.text, "Fix the WAL recovery ordering.");
        assert.equal(result.status, "completed");
      }),
    ),
  );

  it.effect("stops the sidekick when a brief runs past its limit", () =>
    run({ lead: leadProjection(sidekickThreadId), timedOut: true }, (service, calls) =>
      Effect.gen(function* () {
        const result = yield* service.delegateToSidekick(scope, {
          brief: "Build Julia from source.",
        });
        const interrupt = (yield* Ref.get(calls)).find((call) => call.method === "interrupt");
        assert.equal(interrupt?.input.threadId, sidekickThreadId);
        assert.equal(interrupt?.input.runId, sidekickRunId);
        assert.equal(result.status, "timed_out");
        assert.include(result.report, "T3 stopped the sidekick after 30 minutes");
      }),
    ),
  );

  it.effect("refuses a thread without a sidekick", () =>
    run(
      {
        lead: {
          ...leadProjection(null),
          thread: { ...leadProjection(null).thread, sidekick: null },
        } as OrchestrationV2ThreadProjection,
      },
      (service) =>
        Effect.gen(function* () {
          const error = yield* Effect.flip(
            service.delegateToSidekick(scope, { brief: "Anything." }),
          );
          assert.equal(error.code, "sidekick_not_configured");
        }),
    ),
  );
});
