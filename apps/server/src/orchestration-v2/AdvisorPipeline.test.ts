import { assert, it } from "@effect/vitest";
import {
  AdvisorPipelineState as AdvisorPipelineStateSchema,
  AdvisorPlan,
  CommandId,
  EventId,
  MessageId,
  NodeId,
  OrchestrationV2Command,
  ProviderDriverKind,
  ProviderInstanceId,
  ProviderThreadId,
  ThreadId,
  type OrchestrationV2Run,
  type AdvisorConfiguration,
  type AdvisorPipelineState,
  type ModelSelection,
  type RuntimeMode,
} from "@t3tools/contracts";
import * as DateTime from "effect/DateTime";
import * as Effect from "effect/Effect";
import * as NodeServices from "@effect/platform-node/NodeServices";
import * as FileSystem from "effect/FileSystem";
import * as Layer from "effect/Layer";
import * as Schema from "effect/Schema";
import { CheckpointId, CheckpointRef, CheckpointScopeId, TurnItemId } from "@t3tools/contracts";
import * as ServerConfig from "../config.ts";
import * as CheckpointStore from "../checkpointing/CheckpointStore.ts";
import * as CheckpointService from "./CheckpointService.ts";
import * as ProcessRunner from "../processRunner.ts";
import * as VcsDriverRegistry from "../vcs/VcsDriverRegistry.ts";
import * as VcsProcess from "../vcs/VcsProcess.ts";
import * as AdvisorPipelineService from "./AdvisorPipelineService.ts";
import * as ProjectStore from "./ProjectStore.ts";
import * as ThreadManagementService from "./ThreadManagementService.ts";
import * as SqlitePersistence from "../persistence/Sqlite.ts";
import * as Orchestrator from "./Orchestrator.ts";
import * as EventSink from "./EventSink.ts";
import * as EffectOutbox from "./EffectOutbox.ts";
import * as ProviderAdapterRegistry from "./ProviderAdapterRegistry.ts";
import * as ProviderReplayHarness from "./testkit/ProviderReplayHarness.ts";
import { CodexProviderCapabilitiesV2 } from "./Adapters/CodexAdapterV2.ts";
import { acceptanceCommandEnvironment, acceptanceCommandProblem } from "./acceptanceCommands.ts";
import { projectDomainEventForWire, projectThreadProjectionForWire } from "./WireProjection.ts";
import {
  ADVISOR_PLAN_MAX_CHARS,
  advisorPrompt,
  parseAdvisorPlan,
  parseWorkerReport,
  workerPrompt,
} from "./advisorPlan.ts";

const decodeCommand = Schema.decodeUnknownEffect(OrchestrationV2Command);
const decodeStoredPlan = Schema.decodeUnknownSync(AdvisorPlan);
const decodeState = Schema.decodeUnknownSync(AdvisorPipelineStateSchema);

const text = `# Plan
## Goal
Correct addition.
## Files
- \`sum.mjs\`
## Changes
### \`sum.mjs\`
Return a + b.
## Acceptance
Run the existing regression tests.
\`\`\`json
{"commands":[{"command":"node","args":["--test","sum.test.mjs"]}]}
\`\`\``;
const plan = parseAdvisorPlan(text, "/tmp/advisor/plan.md");
if (!plan.ok) throw new Error(plan.reason);
const adapter = (provider: string) => ({
  instanceId: ProviderInstanceId.make(provider),
  driver: ProviderDriverKind.make(provider),
  getCapabilities: () => Effect.succeed(CodexProviderCapabilitiesV2),
  planSelectionTransition: () => Effect.succeed({ type: "apply_on_next_turn" as const }),
  openSession: () => Effect.die("This decision test must not open a provider"),
});
const database = SqlitePersistence.layerMemory;
const layer = ProviderReplayHarness.layerWithRegistry(
  { name: "advisor-pipeline" },
  ProviderAdapterRegistry.layerFromAdapters([
    adapter("claudeAgent"),
    adapter("codex"),
    adapter("grok"),
  ]),
  { databaseLayer: database, runEffectWorker: false },
);
const threadId = ThreadId.make("advisor:pipeline-test");
const create = (
  legacyPauseAfterPlan: boolean | undefined,
  workspace: string | null = null,
  strategy: AdvisorConfiguration["strategy"] | null = "plan-first",
  runtimeMode: RuntimeMode = "full-access",
) =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    yield* orchestrator.dispatch(
      yield* decodeCommand({
        type: "thread.create",
        commandId: "create",
        threadId,
        projectId: "project",
        title: "Advisor test",
        worker: { provider: "claudeAgent", model: "claude-haiku-5-5" },
        modelSelection: { instanceId: "claudeAgent", model: "claude-haiku-5-5" },
        advisor: {
          modelSelection: { instanceId: "codex", model: "gpt-6-sol" },
          ...(legacyPauseAfterPlan === undefined ? {} : { pauseAfterPlan: legacyPauseAfterPlan }),
          ...(strategy === null ? {} : { strategy }),
        },
        runtimeMode,
        interactionMode: "default",
        branch: null,
        worktreePath: workspace,
        createdBy: "user",
        creationSource: "web",
      }),
    );
    yield* orchestrator.dispatch({
      type: "message.dispatch",
      commandId: CommandId.make("task"),
      threadId,
      messageId: MessageId.make("task"),
      text: "Fix addition. ORIGINAL_TASK_MARKER",
      attachments: [],
      createdBy: "user",
      creationSource: "web",
      modelSelection: {
        instanceId: ProviderInstanceId.make("claudeAgent"),
        model: "claude-haiku-5-5",
      },
      usageRole: "worker",
      dispatchMode: { type: "start_immediately" },
    });
    return yield* orchestrator.getThreadProjection(threadId);
  });
const terminal = (
  run: OrchestrationV2Run,
  status: "completed" | "failed" | "cancelled" = "completed",
) =>
  Effect.gen(function* () {
    const sink = yield* EventSink.EventSinkV2;
    const now = yield* DateTime.now;
    yield* sink.write({
      events: [
        {
          id: EventId.make(`terminal:${run.id}`),
          threadId,
          runId: run.id,
          occurredAt: now,
          type: "run.updated",
          payload: { ...run, status, completedAt: now },
        },
      ],
    });
  });
const advance = (
  run: OrchestrationV2Run,
  result: import("@t3tools/contracts").AdvisorAdvanceResult,
) =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    yield* terminal(run);
    yield* orchestrator.dispatch({
      type: "advisor.advance",
      commandId: CommandId.make(`advance:${run.id}`),
      threadId,
      runId: run.id,
      result,
    });
    return yield* orchestrator.getThreadProjection(threadId);
  });

it("validates fixed plan sections, each file's edits, paths and executable acceptance commands", () => {
  assert.isTrue(parseAdvisorPlan(text, "/tmp/plan.md").ok);
  for (const invalid of [
    text.replace("## Goal", "## Aim"),
    text.replace("### `sum.mjs`", "### Other"),
    text.replaceAll("sum.mjs", "../sum.mjs"),
    text.replace('"commands":[', '"commands":[],'),
    text.replace('"node"', '""'),
    text + "x".repeat(24_000),
  ])
    assert.isFalse(parseAdvisorPlan(invalid, "/tmp/plan.md").ok);
});

it("requires an explicit outcome and at least one command in a completed worker report", () => {
  assert.isNull(parseWorkerReport("All done!"));
  assert.isNull(
    parseWorkerReport(
      '<worker_result>{"status":"completed","summary":"done","commands":[]}</worker_result>',
    ),
  );
  assert.isNull(
    parseWorkerReport(
      '<worker_result>{"status":"completed","summary":"done","commands":[{"command":"","args":[]}]}</worker_result>',
    ),
  );
  assert.equal(
    parseWorkerReport(
      '<worker_result>{"status":"needs_advisor","summary":"Cannot locate the entry point","commands":[]}</worker_result>',
    )?.status,
    "needs_advisor",
  );
});

it("accepts file-list descriptions while still enforcing paths and per-file changes", () => {
  const annotated = text.replace("- `sum.mjs`", "- `sum.mjs` — only editable file.");
  const parsed = parseAdvisorPlan(annotated, "/tmp/plan.md");
  assert.isTrue(parsed.ok);
  if (parsed.ok) assert.deepEqual(parsed.plan.files, ["sum.mjs"]);
  assert.isFalse(
    parseAdvisorPlan(annotated.replaceAll("sum.mjs", "../sum.mjs"), "/tmp/plan.md").ok,
  );
  assert.isFalse(
    parseAdvisorPlan(annotated.replace("### `sum.mjs`", "### Other"), "/tmp/plan.md").ok,
  );
});

it("limits new advisor output without invalidating stored legacy plans", () => {
  const maximumPlan = text.padEnd(ADVISOR_PLAN_MAX_CHARS, "x");
  assert.isTrue(parseAdvisorPlan(maximumPlan, "/tmp/plan.md").ok);
  assert.isFalse(parseAdvisorPlan(maximumPlan + "x", "/tmp/plan.md").ok);
  const legacyPlan = { ...plan.plan, markdown: text.padEnd(24_000, "x") };
  assert.deepEqual(decodeStoredPlan(legacyPlan), legacyPlan);
});

it.effect(
  "defaults an unspecified strategy to the worker and completes without an advisor call",
  () =>
    Effect.gen(function* () {
      const initial = yield* create(undefined, null, null);
      const run = initial.runs[0]!;
      assert.equal(run.usageRole, "worker");
      assert.equal(run.modelSelection.model, "claude-haiku-5-5");
      assert.equal(initial.thread.advisorState?.phase, "executing");
      assert.include(workerPrompt(initial.thread.advisorState!), "ORIGINAL_TASK_MARKER");
      const done = yield* advance(run, {
        type: "worker_result",
        passed: true,
        summary: "Checks passed",
        diff: "",
      });
      assert.equal(done.thread.advisorState?.phase, "completed");
      assert.equal(done.runs.length, 1);
    }).pipe(Effect.provide(layer)),
);

it.effect(
  "escalates the first unplanned failure and automatically continues even with a legacy pause preference",
  () =>
    Effect.gen(function* () {
      const initial = yield* create(true, null, "worker-first");
      const next = yield* advance(initial.runs[0]!, {
        type: "worker_result",
        passed: false,
        summary: "Needs help with the failing check",
        diff: "PATCH_MARKER",
      });
      assert.equal(next.runs.length, 2);
      assert.equal(next.runs.at(-1)?.usageRole, "advisor");
      assert.equal(next.thread.advisorState?.cycle, 1);
      assert.include(advisorPrompt(next.thread.advisorState!), "Needs help");
      const resumed = yield* advance(next.runs.at(-1)!, { type: "plan", plan: plan.plan });
      assert.equal(resumed.thread.advisorState?.phase, "executing");
      assert.equal(resumed.thread.advisorState?.failures, 0);
      assert.equal(resumed.runs.length, 3);
      assert.equal(resumed.runs.at(-1)?.usageRole, "worker");
    }).pipe(Effect.provide(layer)),
);

it.effect("honors explicit plan-first routing even when a client requests a worker", () =>
  Effect.gen(function* () {
    const projection = yield* create(false);
    const run = projection.runs.at(-1)!;
    assert.equal(run.usageRole, "advisor");
    assert.equal(run.modelSelection.model, "gpt-6-sol");
    assert.equal(projection.thread.worker?.model, "claude-haiku-5-5");
    assert.equal(projection.thread.advisorState?.phase, "planning");
    assert.equal(projection.messages.at(-1)?.usageRole, "advisor");
    const next = yield* advance(run, { type: "plan", plan: plan.plan });
    assert.equal(next.runs.at(-1)?.usageRole, "worker");
    assert.equal(next.runs.at(-1)?.modelSelection.model, "claude-haiku-5-5");
    assert.notEqual(next.runs.at(-1)?.providerThreadId, run.providerThreadId);
    assert.equal(next.contextHandoffs.length, 0);
    const done = yield* advance(next.runs.at(-1)!, {
      type: "worker_result",
      passed: true,
      summary: "Tests passed",
      diff: "diff",
    });
    assert.equal(done.thread.advisorState?.phase, "completed");
    assert.equal(done.runs.length, 2);
  }).pipe(Effect.provide(layer)),
);

it.effect(
  "automatically starts one worker after planning and ignores a duplicate plan completion",
  () =>
    Effect.gen(function* () {
      const orchestrator = yield* Orchestrator.OrchestratorV2;
      const initial = yield* create(true);
      let next = yield* advance(initial.runs[0]!, { type: "plan", plan: plan.plan });
      assert.equal(next.thread.advisorState?.phase, "executing");
      assert.equal(next.runs.length, 2);
      const command = {
        type: "advisor.advance" as const,
        commandId: CommandId.make(`advance:${initial.runs[0]!.id}`),
        threadId,
        runId: initial.runs[0]!.id,
        result: { type: "plan" as const, plan: plan.plan },
      };
      yield* orchestrator.dispatch(command);
      yield* orchestrator.dispatch(command);
      next = yield* orchestrator.getThreadProjection(threadId);
      assert.equal(next.runs.length, 2);
      assert.equal(next.thread.advisorState?.phase, "executing");
    }).pipe(Effect.provide(layer)),
);

it.effect(
  "returns to a fresh advisor on the third consecutive failure with bounded summary and diff",
  () =>
    Effect.gen(function* () {
      const initial = yield* create(false);
      let next = yield* advance(initial.runs[0]!, { type: "plan", plan: plan.plan });
      const workerThreadId = next.runs.at(-1)!.providerThreadId;
      for (let attempt = 1; attempt <= 3; attempt++) {
        next = yield* advance(next.runs.at(-1)!, {
          type: "worker_result",
          passed: false,
          summary: `Failure ${attempt}`,
          diff: "PATCH_ONLY_MARKER",
        });
        assert.equal(next.runs.at(-1)?.usageRole, attempt === 3 ? "advisor" : "worker");
        if (attempt < 3) assert.equal(next.runs.at(-1)?.providerThreadId, workerThreadId);
        else assert.notEqual(next.runs.at(-1)?.providerThreadId, workerThreadId);
      }
      assert.equal(next.thread.advisorState?.cycle, 1);
      assert.equal(next.thread.advisorState?.failures, 3);
      const prompt = advisorPrompt(next.thread.advisorState!);
      assert.include(prompt, "Failure 3");
      assert.include(prompt, "PATCH_ONLY_MARKER");
      assert.include(prompt, "ORIGINAL_TASK_MARKER");
      assert.include(workerPrompt(next.thread.advisorState!), text);
      assert.equal(next.contextHandoffs.length, 0);
      assert.equal(new Set(next.runs.map((run) => run.providerThreadId)).size, 3);
      next = yield* advance(next.runs.at(-1)!, { type: "plan", plan: plan.plan });
      assert.notEqual(next.runs.at(-1)?.providerThreadId, workerThreadId);
      assert.notEqual(next.runs.at(-1)?.providerThreadId, next.runs.at(-2)?.providerThreadId);
      assert.equal(next.contextHandoffs.length, 0);
    }).pipe(Effect.provide(layer)),
);

it.effect("starts a fresh worker for the next task even when its cycle number is reused", () =>
  Effect.gen(function* () {
    const initial = yield* create(false, null, "worker-first");
    const source = initial.runs[0]!;
    yield* advance(source, { type: "worker_result", passed: true, summary: "Passed", diff: "" });
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    yield* orchestrator.dispatch({
      type: "message.dispatch",
      commandId: CommandId.make("second-task"),
      threadId,
      messageId: MessageId.make("second-task"),
      text: "A new task with different constraints",
      attachments: [],
      createdBy: "user",
      creationSource: "web",
      dispatchMode: { type: "start_immediately" },
    });
    const next = yield* orchestrator.getThreadProjection(threadId);
    assert.equal(next.thread.advisorState?.cycle, 0);
    assert.equal(next.runs.at(-1)?.usageRole, "worker");
    assert.notEqual(next.runs.at(-1)?.providerThreadId, source.providerThreadId);
    assert.equal(next.contextHandoffs.length, 0);
  }).pipe(Effect.provide(layer)),
);

it.effect.each([
  { instanceId: ProviderInstanceId.make("codex"), model: "claude-haiku-5-5" },
  { instanceId: ProviderInstanceId.make("claudeAgent"), model: "different-model" },
  {
    instanceId: ProviderInstanceId.make("claudeAgent"),
    model: "claude-haiku-5-5",
    options: [{ id: "effort", value: "high" }],
  },
] satisfies ModelSelection[])(
  "does not reuse a worker with a different model selection: %j",
  (selection) =>
    Effect.gen(function* () {
      const initial = yield* create(false);
      const executing = yield* advance(initial.runs[0]!, { type: "plan", plan: plan.plan });
      const worker = executing.runs.at(-1)!;
      // Simulate persisted work from an older configuration. Current commands
      // reject model changes while a pipeline is active.
      const next = yield* advance(
        { ...worker, modelSelection: selection },
        { type: "worker_result", passed: false, summary: "Retry", diff: "" },
      );
      assert.notEqual(next.runs.at(-1)?.providerThreadId, worker.providerThreadId);
      assert.equal(next.contextHandoffs.length, 0);
    }).pipe(Effect.provide(layer)),
);

it("sends only bounded repair feedback when the worker's native conversation was resumed", () => {
  const state: AdvisorPipelineState = {
    phase: "executing",
    task: "ORIGINAL_TASK_MUST_SURVIVE_FALLBACK",
    cycle: 0,
    failures: 1,
    currentRunId: null,
    plan: plan.plan,
    summary: "ACCEPTANCE_FAILURE " + "失败".repeat(2_000),
    diff: "CURRENT_DIFF " + "修改".repeat(4_000),
  };
  const base = workerPrompt(state, { resume: true, contextByteBudget: 0 });
  const resumed = workerPrompt(state, { resume: true, contextByteBudget: 512 });
  assert.include(resumed, "ACCEPTANCE_FAILURE");
  assert.include(resumed, "CURRENT_DIFF");
  assert.include(resumed, state.plan!.path);
  assert.notInclude(resumed, state.task!);
  assert.notInclude(resumed, state.plan!.markdown);
  assert.notInclude(resumed, "\uFFFD");
  assert.isAtMost(Buffer.byteLength(resumed) - Buffer.byteLength(base), 512);
  const fresh = workerPrompt(state, { contextByteBudget: 0 });
  assert.include(fresh, state.task!);
  assert.include(fresh, state.plan!.markdown);
});

it.effect.each(["worker-first", "plan-first"] as const)(
  "caps advisor calls after repeated failures in %s mode",
  (strategy) =>
    Effect.gen(function* () {
      let next = yield* create(undefined, null, strategy);
      const failed = {
        type: "worker_result",
        passed: false,
        summary: "Check failed",
        diff: "",
      } as const;
      if (strategy === "worker-first") next = yield* advance(next.runs.at(-1)!, failed);
      const limit = strategy === "worker-first" ? 3 : 4;
      for (let advice = 0; advice < limit; advice++) {
        assert.equal(next.runs.at(-1)?.usageRole, "advisor");
        next = yield* advance(next.runs.at(-1)!, { type: "plan", plan: plan.plan });
        for (let attempt = 0; attempt < 3; attempt++)
          next = yield* advance(next.runs.at(-1)!, failed);
      }
      assert.equal(next.thread.advisorState?.phase, "blocked");
      assert.equal(next.runs.filter((run) => run.usageRole === "advisor").length, limit);
      assert.isTrue(next.runs.every((run) => run.status === "completed"));
      assert.include(next.thread.advisorState!.summary, "Automatic replanning limit reached");
    }).pipe(Effect.provide(layer)),
);

it.effect.each(["worker-first", "plan-first"] as const)(
  "%s: runs the next task on the worker the composer picked",
  (strategy) =>
    Effect.gen(function* () {
      const orchestrator = yield* Orchestrator.OrchestratorV2;
      const initial = yield* create(undefined, null, strategy);
      yield* advance(initial.runs[0]!, { type: "stopped" });
      const luna = { instanceId: ProviderInstanceId.make("codex"), model: "gpt-6-luna" };
      yield* orchestrator.dispatch({
        type: "message.dispatch",
        commandId: CommandId.make("next-task"),
        threadId,
        messageId: MessageId.make("next-task"),
        text: "Fix subtraction.",
        attachments: [],
        createdBy: "user",
        creationSource: "web",
        modelSelection: luna,
        dispatchMode: { type: "start_immediately" },
      });
      const projection = yield* orchestrator.getThreadProjection(threadId);
      assert.deepEqual(projection.thread.worker, luna);
      const run = projection.runs.at(-1)!;
      if (strategy === "worker-first") {
        assert.equal(run.usageRole, "worker");
        assert.deepEqual(run.modelSelection, luna);
      } else {
        assert.equal(run.usageRole, "advisor");
        const next = yield* advance(run, { type: "plan", plan: plan.plan });
        assert.deepEqual(next.runs.at(-1)?.modelSelection, luna);
      }
    }).pipe(Effect.provide(layer)),
);

it.effect("rejects a worker model change while the worker is executing the plan", () =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    const initial = yield* create(true);
    yield* advance(initial.runs[0]!, { type: "plan", plan: plan.plan });
    const rejected = yield* orchestrator
      .dispatch({
        type: "thread.model-selection.set",
        commandId: CommandId.make("change-worker"),
        threadId,
        modelSelection: { instanceId: ProviderInstanceId.make("codex"), model: "gpt-6-luna" },
      })
      .pipe(Effect.result);
    assert.equal(rejected._tag, "Failure");
    assert.equal(
      (yield* orchestrator.getThreadProjection(threadId)).thread.worker?.model,
      "claude-haiku-5-5",
    );
  }).pipe(Effect.provide(layer)),
);

const serviceConfig = ServerConfig.layerTest(process.cwd(), { prefix: "t3-advisor-service-" }).pipe(
  Layer.provide(NodeServices.layer),
);
const vcs = VcsDriverRegistry.layer.pipe(
  Layer.provide(VcsProcess.layer),
  Layer.provide(serviceConfig),
  Layer.provide(NodeServices.layer),
);
const services = Layer.mergeAll(
  layer,
  serviceConfig,
  ProcessRunner.layer,
  CheckpointStore.layer.pipe(Layer.provide(vcs)),
  Layer.mock(ProjectStore.ProjectStoreV2)({ get: () => Effect.succeedNone }),
).pipe(Layer.provideMerge(NodeServices.layer));

const workspaceFixtureFor = (
  strategy: AdvisorConfiguration["strategy"] = "plan-first",
  runtimeMode: RuntimeMode = "full-access",
  projectSubdirectory: string | null = null,
) =>
  Effect.gen(function* () {
    const fs = yield* FileSystem.FileSystem;
    const runner = yield* ProcessRunner.ProcessRunner;
    const checkpoints = yield* CheckpointStore.CheckpointStore;
    const config = yield* ServerConfig.ServerConfig;
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    const sink = yield* EventSink.EventSinkV2;
    const repository = `${config.baseDir}/repo`;
    const cwd = projectSubdirectory === null ? repository : `${repository}/${projectSubdirectory}`;
    yield* fs.makeDirectory(cwd, { recursive: true });
    yield* fs.writeFileString(`${cwd}/sum.mjs`, "export const sum = (a, b) => a - b;\n");
    yield* fs.writeFileString(
      `${cwd}/sum.test.mjs`,
      "import {sum} from './sum.mjs'; import assert from 'node:assert/strict'; assert.equal(sum(2, 3), 5);\n",
    );
    for (const args of [
      ["init"],
      ["add", "."],
      [
        "-c",
        "user.name=Advisor test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "fixture",
      ],
    ]) {
      const output = yield* runner.run({ command: "git", args, cwd: repository });
      assert.equal(output.code, 0);
    }
    // A pre-existing untracked file belongs to the baseline, not the worker.
    yield* fs.writeFileString(`${cwd}/notes.txt`, "Keep this pre-existing scratch file.\n");
    const initial = yield* create(true, cwd, strategy, runtimeMode);
    const advisorRun = initial.runs[0]!;
    const ref = CheckpointRef.make("refs/t3/advisor-test/baseline");
    yield* checkpoints.captureCheckpoint({ cwd, checkpointRef: ref });
    const now = yield* DateTime.now;
    const checkpoint = {
      id: CheckpointId.make("advisor-baseline"),
      threadId,
      scopeId: CheckpointScopeId.make("advisor-test"),
      runId: null,
      nodeId: advisorRun.rootNodeId!,
      parentCheckpointId: null,
      ordinalWithinScope: 0,
      appRunOrdinal: null,
      ref,
      status: "ready" as const,
      files: [],
      capturedAt: now,
    };
    const saveAdvisorCheckpoint = (changed: boolean) =>
      Effect.gen(function* () {
        const afterRef = CheckpointRef.make("refs/t3/advisor-test/after");
        if (changed)
          yield* fs.writeFileString(`${cwd}/sum.mjs`, "export const sum = (a, b) => a + b;\n");
        yield* checkpoints.captureCheckpoint({ cwd, checkpointRef: afterRef });
        yield* sink.write({
          events: [
            {
              id: EventId.make("baseline-event"),
              threadId,
              occurredAt: now,
              type: "checkpoint.captured",
              payload: checkpoint,
            },
            {
              id: EventId.make("advisor-checkpoint-event"),
              threadId,
              runId: advisorRun.id,
              occurredAt: now,
              type: "checkpoint.captured",
              payload: {
                ...checkpoint,
                id: CheckpointId.make("advisor-completed"),
                runId: advisorRun.id,
                ordinalWithinScope: 1,
                appRunOrdinal: 1,
                ref: afterRef,
              },
            },
            {
              id: EventId.make("advisor-plan-item-event"),
              threadId,
              runId: advisorRun.id,
              occurredAt: now,
              type: "turn-item.updated",
              payload: {
                id: TurnItemId.make("advisor-plan-item"),
                threadId,
                runId: advisorRun.id,
                nodeId: advisorRun.rootNodeId,
                providerThreadId: advisorRun.providerThreadId,
                providerTurnId: null,
                nativeItemRef: null,
                parentItemId: null,
                ordinal: 1,
                status: "completed",
                title: null,
                startedAt: now,
                completedAt: now,
                updatedAt: now,
                type: "assistant_message",
                messageId: MessageId.make("advisor-plan"),
                text,
                streaming: false,
              },
            },
          ],
        });
        yield* terminal(advisorRun);
      });
    const pipelineLayer = AdvisorPipelineService.layer.pipe(
      Layer.provide(
        Layer.mock(ThreadManagementService.ThreadManagementService)({
          getThreadRecords: orchestrator.getThreadRecords,
          dispatch: orchestrator.dispatch,
        }),
      ),
    );
    const validate = (run: OrchestrationV2Run) =>
      AdvisorPipelineService.AdvisorPipelineService.use((service) =>
        service.advance({ threadId, runId: run.id }),
      ).pipe(Effect.provide(pipelineLayer));
    const startWorker = Effect.gen(function* () {
      yield* saveAdvisorCheckpoint(false);
      yield* validate(advisorRun);
      const executing = yield* orchestrator.getThreadProjection(threadId);
      assert.equal(executing.thread.advisorState?.phase, "executing");
      assert.equal(yield* fs.readFileString(executing.thread.advisorState!.plan!.path), text);
      assert.equal(executing.runs.at(-1)?.usageRole, "worker");
      return executing.runs.at(-1)!;
    });
    const saveWorkerReport = (reportText: string, subagentText?: string) =>
      Effect.gen(function* () {
        yield* sink.write({
          events: [
            {
              id: EventId.make("initial-worker-baseline"),
              threadId,
              occurredAt: now,
              type: "checkpoint.captured",
              payload: checkpoint,
            },
            {
              id: EventId.make("worker-report-event"),
              threadId,
              runId: advisorRun.id,
              occurredAt: now,
              type: "turn-item.updated",
              payload: {
                id: TurnItemId.make("worker-report"),
                threadId,
                runId: advisorRun.id,
                nodeId: NodeId.make("worker-assistant-message-node"),
                providerThreadId: advisorRun.providerThreadId,
                providerTurnId: null,
                nativeItemRef: null,
                parentItemId: null,
                ordinal: 1,
                status: "completed",
                title: null,
                startedAt: now,
                completedAt: now,
                updatedAt: now,
                type: "assistant_message",
                messageId: MessageId.make("worker-report"),
                text: reportText,
                streaming: false,
              },
            },
          ],
        });
        if (subagentText !== undefined) {
          const projection = yield* orchestrator.getThreadProjection(threadId);
          const report = projection.turnItems.find((item) => item.id === "worker-report")!;
          yield* sink.write({
            events: [
              {
                id: EventId.make("subagent-report-event"),
                threadId,
                runId: advisorRun.id,
                occurredAt: now,
                type: "turn-item.updated",
                payload: {
                  ...report,
                  type: "assistant_message",
                  id: TurnItemId.make("subagent-report"),
                  messageId: MessageId.make("subagent-report"),
                  text: subagentText,
                  streaming: false,
                  providerThreadId: ProviderThreadId.make("subagent-provider-thread"),
                  ordinal: 2,
                },
              },
            ],
          });
        }
        yield* terminal(advisorRun);
      });
    // Emulates RunExecutionService snapshotting the workspace as a run starts.
    const captureRunStart = (run: OrchestrationV2Run) =>
      Effect.gen(function* () {
        const checkpointRef = yield* CheckpointService.pipelineRunStartRef(run.id);
        yield* checkpoints.captureCheckpoint({ cwd, checkpointRef });
      });
    return {
      fs,
      cwd,
      orchestrator,
      captureRunStart,
      validate,
      saveAdvisorCheckpoint,
      advisorRun,
      startWorker,
      saveWorkerReport,
    };
  });
const workspaceFixture = workspaceFixtureFor();

const workerReport =
  '<worker_result>{"status":"completed","summary":"Fixed sum","commands":[{"command":"node","args":["--test","sum.test.mjs"]}]}</worker_result>';

it.effect(
  "reads Codex message nodes, ignores subagent reports and completes without planning",
  () =>
    Effect.gen(function* () {
      const fixture = yield* workspaceFixtureFor("worker-first");
      yield* fixture.fs.writeFileString(
        `${fixture.cwd}/sum.mjs`,
        "export const sum = (a, b) => a + b;\n",
      );
      yield* fixture.saveWorkerReport(
        workerReport,
        '<worker_result>{"status":"needs_advisor","summary":"Unrelated child report","commands":[]}</worker_result>',
      );
      yield* fixture.validate(fixture.advisorRun);
      const done = yield* fixture.orchestrator.getThreadProjection(threadId);
      assert.equal(done.thread.advisorState?.phase, "completed");
      assert.equal(done.runs.length, 1);
      assert.include(done.thread.advisorState!.summary, "exit 0");
      assert.notInclude(done.thread.advisorState!.diff, "notes.txt");
    }).pipe(Effect.provide(services)),
);

it.effect("escalates a failing direct-worker check even when the worker claims completion", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixtureFor("worker-first");
    yield* fixture.saveWorkerReport(workerReport);
    yield* fixture.validate(fixture.advisorRun);
    const next = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(next.thread.advisorState?.phase, "planning");
    assert.equal(next.runs.at(-1)?.usageRole, "advisor");
    assert.include(next.thread.advisorState!.summary, "AssertionError");
    yield* fixture.validate(fixture.advisorRun);
    assert.equal((yield* fixture.orchestrator.getThreadProjection(threadId)).runs.length, 2);
  }).pipe(Effect.provide(services)),
);

it.effect(
  "a missing direct-worker report requests advice instead of claiming acceptance passed",
  () =>
    Effect.gen(function* () {
      const fixture = yield* workspaceFixtureFor("worker-first");
      yield* fixture.saveWorkerReport("Done, everything looks good.");
      yield* fixture.validate(fixture.advisorRun);
      const next = yield* fixture.orchestrator.getThreadProjection(threadId);
      assert.equal(next.thread.advisorState?.phase, "planning");
      assert.include(next.thread.advisorState!.summary, "valid acceptance report");
    }).pipe(Effect.provide(services)),
);

it.effect("does not spend an advisor call on a worker provider failure", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixtureFor("worker-first");
    yield* terminal(fixture.advisorRun, "failed");
    yield* fixture.validate(fixture.advisorRun);
    const next = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(next.thread.advisorState?.phase, "blocked");
    assert.equal(next.runs.length, 1);
    assert.include(next.thread.advisorState!.summary, "provider run failed");
  }).pipe(Effect.provide(services)),
);

it.effect(
  "saves a validated plan and independently accepts worker edits against real Git snapshots and tests",
  () =>
    Effect.gen(function* () {
      const fixture = yield* workspaceFixture;
      const run = yield* fixture.startWorker;
      yield* fixture.fs.writeFileString(
        `${fixture.cwd}/sum.mjs`,
        "export const sum = (a, b) => a + b;\n",
      );
      yield* terminal(run);
      yield* fixture.validate(run);
      const done = yield* fixture.orchestrator.getThreadProjection(threadId);
      assert.equal(done.thread.advisorState?.phase, "completed");
      assert.include(done.thread.advisorState!.summary, "exit 0");
      assert.include(done.thread.advisorState!.diff, "+export const sum = (a, b) => a + b;");
      assert.notInclude(done.thread.advisorState!.summary, "notes.txt");
      yield* fixture.validate(run);
      assert.equal((yield* fixture.orchestrator.getThreadProjection(threadId)).runs.length, 2);
    }).pipe(Effect.provide(services)),
);

it.effect("blocks an advisor that edits the workspace instead of only planning", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixture;
    yield* fixture.saveAdvisorCheckpoint(true);
    yield* fixture.validate(fixture.advisorRun);
    const blocked = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(blocked.thread.advisorState?.phase, "blocked");
    assert.include(blocked.thread.advisorState!.summary, "changed workspace files");
    assert.equal(blocked.runs.length, 1);
  }).pipe(Effect.provide(services)),
);

it.effect(
  "counts real failing acceptance commands and asks a fresh advisor after attempt three",
  () =>
    Effect.gen(function* () {
      const fixture = yield* workspaceFixture;
      let run = yield* fixture.startWorker;
      for (let attempt = 1; attempt <= 3; attempt++) {
        yield* terminal(run);
        yield* fixture.validate(run);
        const next = yield* fixture.orchestrator.getThreadProjection(threadId);
        assert.equal(next.thread.advisorState?.failures, attempt);
        assert.include(next.thread.advisorState!.summary, `Attempt ${attempt}: failed`);
        assert.include(next.thread.advisorState!.summary, "AssertionError");
        run = next.runs.at(-1)!;
        assert.equal(run.usageRole, attempt === 3 ? "advisor" : "worker");
      }
      const next = yield* fixture.orchestrator.getThreadProjection(threadId);
      assert.equal(next.thread.advisorState?.cycle, 1);
      assert.equal(next.contextHandoffs.length, 0);
    }).pipe(Effect.provide(services)),
);

it.effect("rejects files outside the advisor plan even when the acceptance command passes", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixture;
    const run = yield* fixture.startWorker;
    yield* fixture.fs.writeFileString(
      `${fixture.cwd}/sum.mjs`,
      "export const sum = (a, b) => a + b;\n",
    );
    yield* fixture.fs.writeFileString(`${fixture.cwd}/outside.txt`, "OUTSIDE_PLAN_DIFF_MARKER\n");
    yield* terminal(run);
    yield* fixture.validate(run);
    const next = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(next.thread.advisorState?.failures, 1);
    assert.include(
      next.thread.advisorState!.summary,
      "Files outside the advisor plan: outside.txt",
    );
    assert.include(next.thread.advisorState!.diff, "OUTSIDE_PLAN_DIFF_MARKER");
  }).pipe(Effect.provide(services)),
);

it.effect("does not blame the advisor for workspace changes made before its run started", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixture;
    // The user edits a file between the previous turn's checkpoint and this task.
    yield* fixture.fs.writeFileString(`${fixture.cwd}/notes.txt`, "Edited between turns.\n");
    yield* fixture.captureRunStart(fixture.advisorRun);
    yield* fixture.saveAdvisorCheckpoint(false);
    yield* fixture.validate(fixture.advisorRun);
    const executing = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(executing.thread.advisorState?.phase, "executing");
  }).pipe(Effect.provide(services)),
);

it.effect("still blocks an advisor that edits files after its run started", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixture;
    yield* fixture.captureRunStart(fixture.advisorRun);
    yield* fixture.saveAdvisorCheckpoint(true);
    yield* fixture.validate(fixture.advisorRun);
    const blocked = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(blocked.thread.advisorState?.phase, "blocked");
    assert.include(blocked.thread.advisorState!.summary, "changed workspace files");
  }).pipe(Effect.provide(services)),
);

it.effect("checks a direct worker against the workspace it started from", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixtureFor("worker-first");
    yield* fixture.fs.writeFileString(`${fixture.cwd}/notes.txt`, "USER_EDIT_MARKER\n");
    yield* fixture.captureRunStart(fixture.advisorRun);
    yield* fixture.fs.writeFileString(
      `${fixture.cwd}/sum.mjs`,
      "export const sum = (a, b) => a + b;\n",
    );
    yield* fixture.saveWorkerReport(workerReport);
    yield* fixture.validate(fixture.advisorRun);
    const done = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(done.thread.advisorState?.phase, "completed");
    assert.include(done.thread.advisorState!.diff, "sum.mjs");
    assert.notInclude(done.thread.advisorState!.diff, "USER_EDIT_MARKER");
  }).pipe(Effect.provide(services)),
);

it.effect("compares plan files relative to a project inside a larger repository", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixtureFor("plan-first", "full-access", "pkg");
    const run = yield* fixture.startWorker;
    yield* fixture.fs.writeFileString(
      `${fixture.cwd}/sum.mjs`,
      "export const sum = (a, b) => a + b;\n",
    );
    yield* terminal(run);
    yield* fixture.validate(run);
    const done = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.notInclude(done.thread.advisorState!.summary, "Files outside the advisor plan");
    assert.equal(done.thread.advisorState?.phase, "completed");
    assert.include(done.thread.advisorState!.diff, "a/sum.mjs");
  }).pipe(Effect.provide(services)),
);

it.effect("a stopped pipeline and stale completion cannot start another worker", () =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    const initial = yield* create(true);
    const executing = yield* advance(initial.runs[0]!, { type: "plan", plan: plan.plan });
    yield* orchestrator.dispatch({
      type: "thread.stop",
      commandId: CommandId.make("stop"),
      threadId,
    });
    const stale = yield* orchestrator.dispatch({
      type: "advisor.advance",
      commandId: CommandId.make("stale"),
      threadId,
      runId: initial.runs[0]!.id,
      result: { type: "plan", plan: plan.plan },
    });
    // A discarded result republishes nothing to clients.
    assert.lengthOf(stale.storedEvents, 0);
    const next = yield* orchestrator.getThreadProjection(threadId);
    assert.equal(next.thread.advisorState?.phase, "stopped");
    assert.equal(next.runs.length, executing.runs.length);
  }).pipe(Effect.provide(layer)),
);

it.effect("cancelling before the advisor starts cancels its pending provider effect", () =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    const outbox = yield* EffectOutbox.EffectOutboxV2;
    const initial = yield* create(true);
    const pending = (yield* outbox.listByCommandId(CommandId.make("task"))).find(
      (row) => row.request.type === "provider-turn.start",
    );
    assert.equal(pending?.status, "pending");
    yield* orchestrator.dispatch({
      type: "advisor.cancel",
      commandId: CommandId.make("cancel-before-start"),
      threadId,
    });
    const stopped = yield* orchestrator.getThreadProjection(threadId);
    assert.equal(stopped.thread.advisorState?.phase, "stopped");
    assert.equal(stopped.runs[0]?.status, "interrupted");
    const effects = yield* outbox.listByCommandId(CommandId.make("task"));
    assert.equal(effects.find((row) => row.id === pending?.id)?.status, "cancelled");
    yield* orchestrator.dispatch({
      type: "advisor.advance",
      commandId: CommandId.make("cancel-stale-plan"),
      threadId,
      runId: initial.runs[0]!.id,
      result: { type: "plan", plan: plan.plan },
    });
    assert.equal((yield* orchestrator.getThreadProjection(threadId)).runs.length, 1);
  }).pipe(Effect.provide(Layer.merge(layer, EffectOutbox.layer.pipe(Layer.provide(database))))),
);

it("refuses shell, network, deletion and out-of-workspace acceptance commands", () => {
  for (const command of [
    "bash",
    "sh",
    "/usr/bin/node",
    "../bin/node",
    "curl",
    "rm",
    "BASH.EXE",
    "test.cmd",
    "./scripts/check.sh",
    "C:\\node.exe",
    "env",
  ])
    assert.isNotNull(acceptanceCommandProblem({ command, args: [] }), command);
  for (const command of ["node", "pnpm", "./node_modules/.bin/vitest", "python3"])
    assert.isNull(acceptanceCommandProblem({ command, args: ["--test"] }), command);
  // cmd.exe still expands these inside the escaped arguments of a .cmd shim.
  assert.isNotNull(acceptanceCommandProblem({ command: "npm", args: ["%PATH%"] }, "win32"));
  assert.isNull(acceptanceCommandProblem({ command: "npm", args: ["%PATH%"] }, "linux"));

  const unsafePlan = parseAdvisorPlan(text.replace('"node"', '"bash"'), "/tmp/plan.md");
  assert.isFalse(unsafePlan.ok);
  if (!unsafePlan.ok) assert.include(unsafePlan.reason, "`bash` is not allowed");
  const report = parseWorkerReport(
    '<worker_result>{"status":"completed","summary":"done","commands":[{"command":"curl","args":["https://example.invalid"]}]}</worker_result>',
  );
  assert.equal(report?.status, "rejected");
});

it("passes acceptance commands only basic, non-secret environment variables", () => {
  const env = acceptanceCommandEnvironment({
    PATH: "/usr/bin",
    HOME: "/home/user",
    LANG: "en_US.UTF-8",
    LC_ALL: "C",
    TMPDIR: "/tmp",
    OPENAI_API_KEY: "secret",
    GITHUB_TOKEN: "secret",
    NODE_OPTIONS: "--require ./hook.js",
    T3CODE_HOME: "/home/user/.t3",
  });
  assert.deepEqual(
    Object.entries(env).filter(([, value]) => value !== undefined),
    [
      ["PATH", "/usr/bin"],
      ["HOME", "/home/user"],
      ["LANG", "en_US.UTF-8"],
      ["LC_ALL", "C"],
      ["TMPDIR", "/tmp"],
    ],
  );
  // ProcessRunner extends the host environment, so the rest must be unset explicitly.
  assert.isTrue(Object.hasOwn(env, "OPENAI_API_KEY"));
});

it.effect("does not run a worker's acceptance commands unless the thread has full access", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixtureFor("worker-first", "approval-required");
    yield* fixture.saveWorkerReport(workerReport);
    yield* fixture.validate(fixture.advisorRun);
    const blocked = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(blocked.thread.advisorState?.phase, "blocked");
    assert.include(blocked.thread.advisorState!.summary, "full access");
    // The failing check never ran, so no advisor was asked about its failure.
    assert.notInclude(blocked.thread.advisorState!.summary, "AssertionError");
    assert.equal(blocked.runs.length, 1);
  }).pipe(Effect.provide(services)),
);

it.effect("runs acceptance commands without the server's secrets", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixtureFor("worker-first");
    yield* fixture.fs.writeFileString(
      `${fixture.cwd}/env.test.mjs`,
      "import assert from 'node:assert/strict'; assert.equal(process.env.T3_ADVISOR_TEST_SECRET_TOKEN, undefined); assert.ok(process.env.PATH);\n",
    );
    yield* fixture.saveWorkerReport(
      '<worker_result>{"status":"completed","summary":"Checked env","commands":[{"command":"node","args":["--test","env.test.mjs"]}]}</worker_result>',
    );
    process.env.T3_ADVISOR_TEST_SECRET_TOKEN = "must-not-leak";
    yield* fixture
      .validate(fixture.advisorRun)
      .pipe(Effect.ensuring(Effect.sync(() => delete process.env.T3_ADVISOR_TEST_SECRET_TOKEN)));
    const done = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(done.thread.advisorState?.phase, "completed", done.thread.advisorState?.summary);
  }).pipe(Effect.provide(services)),
);

it.effect("blocks a pipeline whose step a restart cancelled instead of reporting it stopped", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixtureFor("worker-first");
    yield* terminal(fixture.advisorRun, "cancelled");
    yield* fixture.validate(fixture.advisorRun);
    const blocked = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(blocked.thread.advisorState?.phase, "blocked");
    assert.include(blocked.thread.advisorState!.summary, "restarted");
    assert.equal(blocked.runs.length, 1);
  }).pipe(Effect.provide(services)),
);

it.effect("never starts a pipeline task from a restart continuation", () =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    const initial = yield* create(true, null, "worker-first");
    const source = initial.runs[0]!;
    yield* advance(source, { type: "blocked", summary: "Restarted" });
    const continuation = yield* orchestrator
      .dispatch({
        type: "message.dispatch",
        commandId: CommandId.make("restart-continuation"),
        threadId,
        messageId: MessageId.make("restart-continuation"),
        text: "T3 Code restarted. Continue.",
        attachments: [],
        createdBy: "agent",
        creationSource: "server",
        restartContinuationOfRunId: source.id,
        dispatchMode: { type: "start_immediately" },
      })
      .pipe(Effect.result);
    assert.equal(continuation._tag, "Failure");
    const after = yield* orchestrator.getThreadProjection(threadId);
    assert.equal(after.runs.length, 1);
    assert.equal(after.thread.advisorState?.phase, "blocked");
  }).pipe(Effect.provide(layer)),
);

it.effect.each([
  { name: "a composer Resume", field: "manualContinuationOfRunId" },
  { name: "a usage-limit resume", field: "usageLimitContinuationOfRunId" },
] as const)("keeps the stopped task when $name targets an advisor run", ({ field }) =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    const initial = yield* create(undefined, null, "worker-first");
    const source = initial.runs[0]!;
    yield* advance(source, { type: "stopped" });
    const resume = yield* orchestrator
      .dispatch({
        type: "message.dispatch",
        commandId: CommandId.make(`resume:${field}`),
        threadId,
        messageId: MessageId.make(`resume:${field}`),
        text: "Continue where you left off.",
        attachments: [],
        createdBy: "user",
        creationSource: "web",
        [field]: source.id,
        dispatchMode: { type: "start_immediately" },
      })
      .pipe(Effect.result);
    if (resume._tag !== "Failure") assert.fail("expected the resume to be refused");
    assert.include(String(resume.failure.cause), "Send the task again");
    const after = yield* orchestrator.getThreadProjection(threadId);
    assert.equal(after.runs.length, 1);
    assert.equal(after.thread.advisorState?.phase, "stopped");
    assert.include(after.thread.advisorState?.task ?? "", "ORIGINAL_TASK_MARKER");
  }).pipe(Effect.provide(layer)),
);

it.effect("stop cancels acceptance checks still validating the last run", () =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    const outbox = yield* EffectOutbox.EffectOutboxV2;
    const initial = yield* create(true, null, "worker-first");
    const run = initial.runs[0]!;
    yield* terminal(run);
    const validation = () =>
      outbox
        .listByCommandId(CommandId.make(`command:advisor.advance:${run.id}`))
        .pipe(Effect.map((rows) => rows.find((row) => row.request.type === "advisor.advance")));
    assert.equal((yield* validation())?.status, "pending");
    yield* orchestrator.dispatch({
      type: "thread.stop",
      commandId: CommandId.make("stop-validation"),
      threadId,
    });
    assert.equal((yield* validation())?.status, "cancelled");
    assert.equal(
      (yield* orchestrator.getThreadProjection(threadId)).thread.advisorState?.phase,
      "stopped",
    );
  }).pipe(Effect.provide(Layer.merge(layer, EffectOutbox.layer.pipe(Layer.provide(database))))),
);

it.effect("keeps the task and worker diff on the server", () =>
  Effect.gen(function* () {
    const orchestrator = yield* Orchestrator.OrchestratorV2;
    const initial = yield* create(false);
    const executing = yield* advance(initial.runs[0]!, { type: "plan", plan: plan.plan });
    const failed = yield* advance(executing.runs.at(-1)!, {
      type: "worker_result",
      passed: false,
      summary: "Check failed",
      diff: "PATCH_ONLY_MARKER",
    });
    assert.include(failed.thread.advisorState!.task!, "ORIGINAL_TASK_MARKER");
    assert.equal(failed.thread.advisorState!.diff, "PATCH_ONLY_MARKER");
    assert.equal(initial.thread.advisorState!.taskMessageId, MessageId.make("task"));
    assert.equal(failed.thread.advisorState!.taskMessageId, MessageId.make("task"));
    const wire = projectThreadProjectionForWire(failed).thread.advisorState!;
    assert.isFalse("task" in wire || "diff" in wire || "taskMessageId" in wire);
    assert.deepEqual(wire.plan, plan.plan);
    assert.equal(wire.summary, "Check failed");
    const events = (yield* orchestrator.dispatch({
      type: "thread.stop",
      commandId: CommandId.make("stop-wire"),
      threadId,
    })).storedEvents.map((stored) => projectDomainEventForWire(stored.event));
    const metadata = events.find((event) => event.type === "thread.metadata-updated");
    assert.isDefined(metadata);
    if (metadata?.type === "thread.metadata-updated") {
      assert.isFalse("task" in metadata.payload.advisorState!);
      assert.isFalse("diff" in metadata.payload.advisorState!);
    }
    // Events persisted before the split still carry both and decode.
    assert.equal(decodeState({ ...wire, task: "old task", diff: "old diff" }).task, "old task");
  }).pipe(Effect.provide(layer)),
);

it.effect("blocks the pipeline when its next step cannot be dispatched", () =>
  Effect.gen(function* () {
    const fixture = yield* workspaceFixtureFor("worker-first");
    yield* fixture.saveWorkerReport(workerReport);
    // Fresh: the harness already built the service with the real dispatcher.
    const refusing = Layer.fresh(AdvisorPipelineService.layer).pipe(
      Layer.provide(
        Layer.mock(ThreadManagementService.ThreadManagementService)({
          getThreadRecords: fixture.orchestrator.getThreadRecords,
          dispatch: (command) =>
            command.commandId.endsWith(":blocked")
              ? fixture.orchestrator.dispatch(command)
              : Effect.fail(
                  new Orchestrator.OrchestratorDispatchError({
                    commandId: command.commandId,
                    commandType: command.type,
                    cause: new Error("The advisor model is unavailable."),
                  }),
                ),
        }),
      ),
    );
    yield* AdvisorPipelineService.AdvisorPipelineService.use((service) =>
      service.advance({ threadId, runId: fixture.advisorRun.id }),
    ).pipe(Effect.provide(refusing));
    const blocked = yield* fixture.orchestrator.getThreadProjection(threadId);
    assert.equal(blocked.thread.advisorState?.phase, "blocked");
    assert.include(blocked.thread.advisorState!.summary, "The advisor model is unavailable.");
  }).pipe(Effect.provide(services)),
);
