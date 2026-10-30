import { assert, it } from "@effect/vitest";
import {
  CommandId,
  OrchestrationV2AppThread,
  OrchestrationV2Command,
  ProviderDriverKind,
  ProviderInstanceId,
  ThreadId,
} from "@t3tools/contracts";
import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import * as Layer from "effect/Layer";
import * as SqlClient from "effect/sql/SqlClient";
import * as SqlitePersistence from "../persistence/Sqlite.ts";
import * as Orchestrator from "./Orchestrator.ts";
import * as ProjectionStore from "./ProjectionStore.ts";
import * as ProviderAdapterRegistry from "./ProviderAdapterRegistry.ts";
import * as ProviderReplayHarness from "./testkit/ProviderReplayHarness.ts";
import { CodexProviderCapabilitiesV2 } from "./Adapters/CodexAdapterV2.ts";

const encodeThread = Schema.encodeEffect(OrchestrationV2AppThread);

const adapter = (provider: string) => ({
  instanceId: ProviderInstanceId.make(provider),
  driver: ProviderDriverKind.make(provider),
  getCapabilities: () => Effect.succeed(CodexProviderCapabilitiesV2),
  planSelectionTransition: () => Effect.succeed({ type: "apply_on_next_turn" as const }),
  openSession: () => Effect.die("This metadata test must never open a provider"),
});
const database = SqlitePersistence.layerMemory;
const harness = ProviderReplayHarness.layerWithRegistry(
  { name: "worker-config" },
  ProviderAdapterRegistry.layerFromAdapters([adapter("claudeAgent"), adapter("codex")]),
  { databaseLayer: database, runEffectWorker: false },
);

const layer = Layer.mergeAll(
  database,
  ProjectionStore.layer.pipe(Layer.provide(database)),
  harness,
);

it.effect(
  "legacy worker commands preserve storage and wire compatibility while switching providers",
  () =>
    Effect.gen(function* () {
      const orchestrator = yield* Orchestrator.OrchestratorV2;
      const store = yield* ProjectionStore.ProjectionStoreV2;
      const sql = yield* SqlClient.SqlClient;
      const decode = Schema.decodeUnknownEffect(OrchestrationV2Command);
      const threadId = ThreadId.make("worker:config-test");
      const create = yield* decode({
        type: "thread.create",
        commandId: "worker-create",
        threadId,
        projectId: "worker-project",
        title: "Worker configuration",
        worker: { provider: "claudeAgent", model: "claude-haiku-5-5" },
        modelSelection: { instanceId: "codex", model: "gpt-6-luna" },
        runtimeMode: "full-access",
        interactionMode: "default",
        branch: null,
        worktreePath: null,
        createdBy: "user",
        creationSource: "web",
      });
      yield* orchestrator.dispatch(create);
      let thread = yield* store.getThread(threadId);
      assert.deepStrictEqual(thread.worker, {
        instanceId: ProviderInstanceId.make("claudeAgent"),
        model: "claude-haiku-5-5",
      });
      assert.equal(thread.modelSelection.instanceId, "claudeAgent");
      const legacyWorker = { provider: "claudeAgent", model: "claude-haiku-5-5" };
      const wireThread = yield* encodeThread(thread);
      assert.deepStrictEqual(wireThread.worker, legacyWorker);
      assert.deepStrictEqual(wireThread.modelSelection, {
        instanceId: "claudeAgent",
        model: "claude-haiku-5-5",
      });
      const stored = yield* sql<{ payload_json: string }>`
        SELECT payload_json FROM orchestration_v2_projection_threads WHERE thread_id = ${threadId}
      `;
      assert.deepStrictEqual(JSON.parse(stored[0]!.payload_json).worker, legacyWorker);
      const events = yield* sql<{ payload_json: string }>`
        SELECT payload_json FROM orchestration_events
        WHERE stream_id = ${threadId} AND event_type = 'thread.created'
      `;
      assert.deepStrictEqual(JSON.parse(events[0]!.payload_json).worker, legacyWorker);
      const update = yield* decode({
        type: "thread.model-selection.set",
        commandId: "worker-switch",
        threadId,
        worker: { provider: "codex", model: "gpt-6-luna" },
        modelSelection: { instanceId: "codex", model: "gpt-6-luna" },
      });
      yield* orchestrator.dispatch(update);
      thread = yield* store.getThread(threadId);
      assert.deepStrictEqual(thread.worker, {
        instanceId: ProviderInstanceId.make("codex"),
        model: "gpt-6-luna",
      });
      assert.equal(thread.providerInstanceId, "codex");
      assert.equal(thread.modelSelection.model, "gpt-6-luna");
      yield* orchestrator.dispatch({
        type: "thread.model-selection.set",
        commandId: CommandId.make("worker-old-selector"),
        threadId,
        modelSelection: {
          instanceId: ProviderInstanceId.make("claudeAgent"),
          model: "claude-haiku-5-5",
        },
      });
      thread = yield* store.getThread(threadId);
      assert.deepStrictEqual(thread.worker, {
        instanceId: ProviderInstanceId.make("claudeAgent"),
        model: "claude-haiku-5-5",
      });
      const message = yield* decode({
        type: "message.dispatch",
        commandId: "worker-message-switch",
        threadId,
        messageId: "worker-message",
        text: "Metadata only",
        attachments: [],
        createdBy: "user",
        creationSource: "web",
        usageRole: "advisor",
        modelSelection: { instanceId: "codex", model: "gpt-6-luna" },
        dispatchMode: { type: "start_immediately" },
      });
      yield* orchestrator.dispatch(message);
      const projection = yield* store.getThreadProjection(threadId);
      assert.deepStrictEqual(projection.thread.worker, {
        instanceId: ProviderInstanceId.make("codex"),
        model: "gpt-6-luna",
      });
      assert.equal(projection.runs.at(-1)?.usageRole, "advisor");
    }).pipe(Effect.provide(layer)),
);
