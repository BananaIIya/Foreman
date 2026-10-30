import {
  CheckpointScopeId,
  CommandId,
  RunId,
  ThreadId,
  type AdvisorAdvanceResult,
} from "@t3tools/contracts";
import * as Context from "effect/Context";
import * as Crypto from "effect/Crypto";
import * as Effect from "effect/Effect";
import * as FileSystem from "effect/FileSystem";
import * as Layer from "effect/Layer";
import * as Option from "effect/Option";
import * as PlatformError from "effect/PlatformError";
import * as Path from "effect/Path";
import * as Schema from "effect/Schema";
import * as HostProcess from "@t3tools/shared/HostProcess";
import * as Config from "../config.ts";
import * as CheckpointStore from "../checkpointing/CheckpointStore.ts";
import type { CheckpointStoreError } from "../checkpointing/Errors.ts";
import * as ProcessRunner from "../processRunner.ts";
import { checkpointRefForScopeOrdinal, pipelineRunStartRef } from "./CheckpointService.ts";
import * as ProjectStore from "./ProjectStore.ts";
import * as ThreadManagementService from "./ThreadManagementService.ts";
import { userFacingDispatchErrorMessage } from "./UserFacingErrors.ts";
import { acceptanceCommandEnvironment, acceptanceCommandProblem } from "./acceptanceCommands.ts";
import { parseAdvisorPlan, parseWorkerReport } from "./advisorPlan.ts";

export class AdvisorPipelineError extends Schema.TaggedError<AdvisorPipelineError>()(
  "AdvisorPipelineError",
  {
    threadId: ThreadId,
    runId: RunId,
    cause: Schema.Defect(),
  },
) {
  override get message() {
    return "Unable to advance the advisor pipeline.";
  }
}

export class AdvisorPipelineService extends Context.Service<
  AdvisorPipelineService,
  {
    readonly advance: (input: {
      readonly threadId: ThreadId;
      readonly runId: RunId;
    }) => Effect.Effect<void, AdvisorPipelineError>;
  }
>()("t3/orchestration-v2/AdvisorPipelineService") {}

const make = Effect.gen(function* () {
  const threads = yield* ThreadManagementService.ThreadManagementService;
  const projects = yield* ProjectStore.ProjectStoreV2;
  const runner = yield* ProcessRunner.ProcessRunner;
  const checkpoints = yield* CheckpointStore.CheckpointStore;
  const crypto = yield* Crypto.Crypto;
  const fs = yield* FileSystem.FileSystem;
  const path = yield* Path.Path;
  const config = yield* Config.ServerConfig;
  const hostEnvironment = yield* HostProcess.Environment;
  const platform = yield* HostProcess.Platform;
  const advance: AdvisorPipelineService["Service"]["advance"] = Effect.fn(
    "AdvisorPipelineService.advance",
  )(
    function* (input) {
      const records = yield* threads.getThreadRecords(
        input.threadId,
        ["runs", "turnItems", "checkpoints"],
        {
          turnItemRunIds: [input.runId],
          turnItemTypes: ["proposed_plan", "assistant_message"],
        },
      );
      const { thread } = records;
      const state = thread.advisorState;
      const run = records.runs.find((row) => row.id === input.runId);
      if (
        !thread.advisor ||
        !state ||
        !run ||
        state.currentRunId !== run.id ||
        run.advisorCycle !== state.cycle ||
        thread.archivedAt !== null ||
        thread.deletedAt !== null ||
        !["planning", "executing"].includes(state.phase)
      )
        return;
      const result = yield* Effect.gen(function* (): Effect.fn.Return<
        AdvisorAdvanceResult,
        | ProcessRunner.ProcessRunError
        | PlatformError.PlatformError
        | ProjectStore.ProjectStoreV2Error
        | CheckpointStoreError,
        Crypto.Crypto
      > {
        if (run.status === "interrupted") return { type: "stopped" };
        // Only process loss or archiving cancels a started pipeline run. A
        // restart never continues a pipeline, so say how to pick it up.
        if (run.status === "cancelled")
          return {
            type: "blocked",
            summary:
              "T3 Code restarted while this step was running, so the pipeline stopped. Send the task again to start over.",
          };
        if (run.status !== "completed" && run.status !== "failed")
          return { type: "blocked", summary: "The provider run did not complete." };
        const project = yield* projects.get(thread.projectId);
        const cwd =
          thread.worktreePath ?? (Option.isSome(project) ? project.value.workspaceRoot : undefined);
        if (!cwd) return { type: "blocked", summary: "The pipeline needs a project workspace." };
        // Runs started before start snapshots existed fall back to the turn baseline.
        const startRef = yield* pipelineRunStartRef(run.id);
        const started = yield* checkpoints
          .hasCheckpointRef({ cwd, checkpointRef: startRef })
          .pipe(Effect.orElseSucceed(() => false));
        const turnBaseline = records.checkpoints.find(
          (row) => row.ordinalWithinScope === run.ordinal - 1 && row.status === "ready",
        )?.ref;
        const startedFrom = started ? startRef : turnBaseline;
        if (run.usageRole === "advisor") {
          if (run.status === "failed")
            return {
              type: "blocked",
              summary: "Advisor planning failed. Resolve the provider error and start a new task.",
            };
          const after = records.checkpoints.find(
            (row) => row.runId === run.id && row.status === "ready",
          );
          if (!startedFrom || !after)
            return {
              type: "blocked",
              summary: "Advisor planning needs before/after workspace checkpoints.",
            };
          const edits = yield* runner
            .run({
              command: "git",
              args: ["diff", "--name-only", "--no-renames", "-z", startedFrom, after.ref, "--"],
              cwd,
              maxOutputBytes: 32_000,
            })
            .pipe(
              Effect.ensuring(
                checkpoints
                  .deleteCheckpointRefs({ cwd, checkpointRefs: [startRef] })
                  .pipe(Effect.ignore),
              ),
            );
          if (edits.code !== 0)
            return {
              type: "blocked",
              summary: "Advisor workspace checkpoints could not be compared.",
            };
          if (edits.stdout)
            return {
              type: "blocked",
              summary:
                "The advisor changed workspace files during planning. Review the diff and start a new task.",
            };
          const candidates = records.turnItems
            .filter((item) => item.type === "proposed_plan" || item.type === "assistant_message")
            .toReversed();
          const directory = path.join(
            config.stateDir,
            "advisor-plans",
            encodeURIComponent(thread.id),
            encodeURIComponent(run.id),
          );
          const planPath = path.join(directory, "plan.md");
          const attempts = candidates.map((item) =>
            parseAdvisorPlan(
              item.type === "proposed_plan"
                ? item.markdown
                : item.type === "assistant_message"
                  ? item.text
                  : "",
              planPath,
            ),
          );
          const parsed = attempts.find((candidate) => candidate.ok);
          if (!parsed?.ok) {
            const latest = attempts[0];
            return {
              type: "blocked",
              summary: `Advisor did not produce a valid plan.md. Required sections: Goal, Files, Changes, Acceptance with executable JSON commands.${latest && !latest.ok ? `\n${latest.reason}` : ""}`,
            };
          }
          yield* fs.makeDirectory(directory, { recursive: true });
          yield* fs.writeFileString(`${planPath}.tmp`, parsed.plan.markdown);
          yield* fs.rename(`${planPath}.tmp`, planPath);
          return { type: "plan", plan: parsed.plan };
        }
        if (run.status === "failed")
          return {
            type: "blocked",
            summary:
              "The worker provider run failed. Resolve the provider error before starting a new task; advisor help cannot fix provider availability.",
          };
        const finalMessage = records.turnItems.findLast(
          (item) =>
            item.type === "assistant_message" &&
            item.status === "completed" &&
            // Codex messages have their own execution nodes beneath the run root.
            // Select the main provider conversation, excluding subagent reports.
            item.providerThreadId === run.providerThreadId &&
            item.parentItemId === null,
        );
        const report =
          !state.plan && finalMessage?.type === "assistant_message"
            ? parseWorkerReport(finalMessage.text)
            : null;
        const checks = [];
        let passed = state.plan !== null || report?.status === "completed";
        if (!state.plan) {
          checks.push(
            report?.status === "rejected"
              ? `Worker report: ${report.summary}\nIts acceptance commands were not run. ${report.reason}`
              : report
                ? `${report.status === "needs_advisor" ? "Worker requested advisor help" : "Worker report"}: ${report.summary}`
                : `Worker did not provide a valid acceptance report.\n${finalMessage?.type === "assistant_message" ? finalMessage.text.slice(-3_000) : "No final report."}`,
          );
        }
        const advisorRun = records.runs.findLast(
          (row) => row.advisorCycle === state.cycle && row.usageRole === "advisor",
        );
        // A planned worker is checked against the workspace the plan was written
        // for, so retries accumulate; a direct worker against where it started.
        const baseline = state.plan
          ? records.checkpoints.find(
              (row) => row.status === "ready" && row.runId === advisorRun?.id,
            )?.ref
          : startedFrom;
        if (!baseline)
          return {
            type: "blocked",
            summary: "Worker validation needs the workspace checkpoint from before execution.",
          };
        const commands =
          state.plan?.commands ?? (report?.status === "completed" ? report.commands : []);
        // Acceptance commands are model output. Only a thread the user gave
        // full access may run them on this machine.
        if (commands.length > 0 && thread.runtimeMode !== "full-access")
          return {
            type: "blocked",
            summary:
              "Acceptance checks were not run: they execute commands the model chose, so they run only on threads with full access. Review the changes yourself, or switch this thread to full access and send the task again.",
          };
        const env = acceptanceCommandEnvironment({ ...process.env, ...hostEnvironment });
        for (const command of commands) {
          // Plans saved before this policy existed are checked again here.
          const problem = acceptanceCommandProblem(command, platform);
          if (problem !== null) {
            passed = false;
            checks.push(`Acceptance command was not run: ${problem}`);
            continue;
          }
          const output = yield* runner
            .run({
              ...command,
              cwd,
              env,
              timeout: "60 seconds",
              timeoutBehavior: "timedOutResult",
              maxOutputBytes: 4_000,
              outputMode: "truncate",
            })
            .pipe(Effect.result);
          if (output._tag === "Failure") {
            passed = false;
            checks.push(`Acceptance command could not run: ${command.command}`);
          } else {
            const ok = output.success.code === 0 && !output.success.timedOut;
            passed &&= ok;
            checks.push(
              `${command.command} ${command.args.join(" ")}: ${output.success.timedOut ? "timed out" : `exit ${output.success.code}`}\n${output.success.stdout}\n${output.success.stderr}`,
            );
          }
        }
        // Use a checkpoint tree so pre-existing untracked files are not mistaken for worker edits.
        const validationRef = yield* checkpointRefForScopeOrdinal({
          scopeId: CheckpointScopeId.make(`advisor-validation:${run.id}`),
          ordinalWithinScope: state.cycle,
        });
        const compared = yield* Effect.gen(function* () {
          yield* checkpoints.captureCheckpoint({ cwd, checkpointRef: validationRef });
          const names = yield* runner.run({
            command: "git",
            // Plan files are relative to the workspace, which can be a
            // subdirectory of its repository.
            args: [
              "diff",
              "--name-only",
              "--no-renames",
              "--relative",
              "-z",
              baseline,
              validationRef,
              "--",
            ],
            cwd,
            maxOutputBytes: 32_000,
          });
          const diff = yield* runner.run({
            command: "git",
            args: ["diff", "--no-ext-diff", "--relative", baseline, validationRef, "--"],
            cwd,
            maxOutputBytes: 12_000,
            outputMode: "truncate",
          });
          return { names, diff };
        }).pipe(
          Effect.ensuring(
            checkpoints
              .deleteCheckpointRefs({ cwd, checkpointRefs: [validationRef, startRef] })
              .pipe(Effect.ignore),
          ),
        );
        if (compared.names.code !== 0 || compared.diff.code !== 0)
          return {
            type: "blocked",
            summary: "Worker workspace checkpoints could not be compared.",
          };
        const outsidePlan = compared.names.stdout
          .split("\0")
          .filter((file) => file && state.plan && !state.plan.files.includes(file));
        if (outsidePlan.length) {
          passed = false;
          checks.push(`Files outside the advisor plan: ${outsidePlan.join(", ")}`);
        }
        const summary = [
          ...(state.failures > 0 ? [state.summary] : []),
          `Attempt ${state.failures + 1}: ${passed ? "passed" : "failed"}`,
          ...checks,
        ]
          .join("\n")
          .slice(-6_000);
        return {
          type: "worker_result",
          passed,
          summary,
          diff: compared.diff.stdout.slice(0, 12_000),
        };
      }).pipe(
        Effect.provideService(Crypto.Crypto, crypto),
        Effect.catch(() =>
          Effect.succeed<AdvisorAdvanceResult>({
            type: "blocked",
            summary:
              "Pipeline validation could not finish. Check the workspace and start a new task.",
          }),
        ),
      );
      const command = {
        type: "advisor.advance",
        commandId: CommandId.make(`command:advisor.advance:${run.id}`),
        threadId: thread.id,
        runId: run.id,
        result,
      } as const;
      // A refused next step must not leave the pipeline "executing" with no run.
      // The refused command id now has a rejected receipt, so record it apart.
      yield* threads.dispatch(command).pipe(
        Effect.catch((error) =>
          threads.dispatch({
            ...command,
            ...(error._tag === "ThreadManagementProviderInstanceDisabledError"
              ? {}
              : { commandId: CommandId.make(`${command.commandId}:blocked`) }),
            result: {
              type: "blocked",
              summary:
                error._tag === "ThreadManagementProviderInstanceDisabledError"
                  ? "The next advisor or worker is disabled for this project. Enable it and start a new task."
                  : `The next pipeline step could not start: ${userFacingDispatchErrorMessage(error) ?? error.message}`.slice(
                      0,
                      6_000,
                    ),
            },
          }),
        ),
      );
    },
    (effect, input) =>
      effect.pipe(Effect.mapError((cause) => new AdvisorPipelineError({ ...input, cause }))),
  );
  return AdvisorPipelineService.of({ advance });
});

export const layer = Layer.effect(AdvisorPipelineService, make);
