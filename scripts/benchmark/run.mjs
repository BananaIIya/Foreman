import * as NodeFSP from "node:fs/promises";
import * as NodePath from "node:path";
import * as NodeCrypto from "node:crypto";
import * as NodeChildProcess from "node:child_process";
import * as NodeUtil from "node:util";
import { tasks, coldTasks } from "./tasks.mjs";
import { writeFixture, evaluate, hash } from "./evaluate.mjs";
import { getProtocol, schedule, accountedCost } from "./protocol.mjs";
import { T3Rpc } from "./rpc.mjs";

const { values } = NodeUtil.parseArgs({
  options: {
    phase: { type: "string", default: "cold" },
    profile: { type: "string", default: "claude" },
    strategy: { type: "string", default: "plan-first" },
    groups: { type: "string", default: "A,B,C" },
    out: { type: "string" },
    workspaces: { type: "string" },
    session: { type: "string" },
    url: { type: "string", default: "http://127.0.0.1:13773" },
    take: { type: "string" },
  },
});
const protocol = getProtocol(values.profile);
if (!["worker-first", "plan-first"].includes(values.strategy))
  throw Error("--strategy must be worker-first or plan-first");
const selectedGroups = values.groups.split(",");
if (
  !selectedGroups.length ||
  new Set(selectedGroups).size !== selectedGroups.length ||
  selectedGroups.some((group) => !["A", "B", "C"].includes(group))
)
  throw Error("--groups must be a unique comma-separated subset of A,B,C");
const matrix = schedule().filter((row) => selectedGroups.includes(row.group));
protocol.advisorStrategy = values.strategy;
protocol.retries =
  values.strategy === "worker-first"
    ? "Luna/weak worker first; first unplanned failure consults advisor automatically, then 3 worker failures per replan, at most 3 advisor calls. No external retries or hidden feedback."
    : "Advisor first; automatic worker handoff, 3 worker failures per replan, up to 3 replans. No external retries or hidden feedback.";
const weak = protocol.groups.C.worker;
if (
  !values.out ||
  !values.session ||
  !values.workspaces ||
  !["cold", "matrix"].includes(values.phase)
)
  throw Error(
    "Usage: node scripts/benchmark/run.mjs --phase cold|matrix --out /absolute/results --workspaces /absolute/fixtures --session /path/session.json",
  );
if (values.take && !/^[1-9]\d*$/.test(values.take))
  throw Error("--take must be a positive integer");
const out = NodePath.resolve(values.out),
  workspaces = NodePath.resolve(values.workspaces);
if (out === workspaces || out.startsWith(workspaces + NodePath.sep))
  throw Error("Results and hidden evaluation data must stay outside agent workspaces");
await NodeFSP.mkdir(out, { recursive: true });
const lock = await NodeFSP.open(NodePath.join(out, "runner.lock"), "wx").catch(() => {
  throw Error(
    "Runner lock exists. Inspect the recorded PID and unfinished trials before removing a stale lock.",
  );
});
await lock.writeFile(JSON.stringify({ pid: process.pid, startedAt: new Date().toISOString() }));
const atomic = async (name, data) => {
  const target = NodePath.join(out, name),
    temporary = target + "." + NodeCrypto.randomUUID() + ".tmp";
  await NodeFSP.writeFile(temporary, JSON.stringify(data, null, 2) + "\n");
  await NodeFSP.rename(temporary, target);
};
const read = async (name) => JSON.parse(await NodeFSP.readFile(NodePath.join(out, name), "utf8"));
const exists = async (name) =>
  NodeFSP.access(NodePath.join(out, name)).then(
    () => true,
    () => false,
  );
const active = new Map(),
  results = new Map();
let rpc,
  shutdown = false,
  monitorError = null;
let monitorPending = Promise.resolve();
const log = (data) => console.log(JSON.stringify({ at: new Date().toISOString(), ...data }));
const terminal = (run) => ["completed", "failed", "cancelled", "interrupted"].includes(run.status);
const done = (row, state) =>
  row.group === "B"
    ? ["completed", "blocked", "stopped"].includes(state.thread.advisorState?.phase) &&
      state.runs.every(terminal)
    : state.runs.length > 0 && state.runs.every(terminal);

async function stop(row, reason) {
  if (row.stopAcknowledged) return;
  row.stopReason ??= reason;
  row.stopCommandId ??= NodeCrypto.randomUUID();
  await atomic(row.id + ".trial.json", row);
  log({ id: row.id, stopping: reason });
  await rpc.rpc("orchestration.dispatchCommand", {
    type: "thread.stop",
    commandId: row.stopCommandId,
    threadId: row.threadId,
  });
  row.stopAcknowledged = true;
  await atomic(row.id + ".trial.json", row);
}

async function usage() {
  const summary = await rpc.rpc("server.getUsageSummary", {
    sinceDay: "2026-10-12",
    untilDay: new Date().toISOString().slice(0, 10),
    timeZone: "UTC",
    awaitRefresh: true,
  });
  return new Map(summary.threadUsage.map((row) => [row.threadId, row.turns]));
}

async function runTrial(entry) {
  if (results.has(entry.id)) return;
  const task = [...tasks, ...coldTasks].find((t) => t.id === entry.taskId);
  let row;
  if (await exists(entry.id + ".trial.json")) {
    row = await read(entry.id + ".trial.json");
  } else {
    const cwd = NodePath.join(workspaces, entry.id);
    // Never silently overwrite a previous run's workspace.
    await NodeFSP.mkdir(cwd, { recursive: false });
    await writeFixture(task, cwd);
    const git = (args) =>
      NodeChildProcess.execFileSync("git", args, { cwd, encoding: "utf8" }).trim();
    git(["init", "-q"]);
    git(["add", "."]);
    git([
      "-c",
      "user.name=M4 Benchmark",
      "-c",
      "user.email=benchmark@example.invalid",
      "commit",
      "-qm",
      "Benchmark fixture",
    ]);
    row = {
      ...entry,
      cwd,
      projectId: NodeCrypto.randomUUID(),
      threadId: NodeCrypto.randomUUID(),
      createdAt: new Date().toISOString(),
      baseCommit: git(["rev-parse", "HEAD"]),
      commands: Object.fromEntries(
        ["project", "thread", "advisor", "message", "messageId"].map((k) => [
          k,
          NodeCrypto.randomUUID(),
        ]),
      ),
    };
    await atomic(row.id + ".trial.json", row);
  }
  active.set(row.id, row);
  const group = row.group === "cold" ? { worker: weak } : protocol.groups[row.group];
  try {
    await rpc.rpc("projects.mutate", {
      type: "project.create",
      commandId: row.commands.project,
      projectId: row.projectId,
      title: "M4 " + (values.profile === "gpt" ? "GPT " : "") + row.id,
      workspaceRoot: row.cwd,
    });
    await rpc.rpc("orchestration.dispatchCommand", {
      type: "thread.create",
      commandId: row.commands.thread,
      threadId: row.threadId,
      projectId: row.projectId,
      title: "M4 " + (values.profile === "gpt" ? "GPT " : "") + row.id,
      modelSelection: group.worker,
      worker: {
        provider: group.worker.instanceId,
        model: group.worker.model,
        options: group.worker.options,
      },
      runtimeMode: "full-access",
      interactionMode: "default",
      branch: null,
      worktreePath: null,
      createdBy: "user",
      creationSource: "web",
    });
    if (group.advisor)
      await rpc.rpc("orchestration.dispatchCommand", {
        type: "thread.advisor.set",
        commandId: row.commands.advisor,
        threadId: row.threadId,
        advisor: { modelSelection: group.advisor, strategy: values.strategy },
      });
    rpc.watch(row.threadId);
    await rpc.waitFor(row.threadId, () => true, 60_000);
    if (!row.startedAt) {
      row.startedAt = new Date().toISOString();
      await atomic(row.id + ".trial.json", row);
    }
    log({ id: row.id, phase: "running", threadId: row.threadId });
    // Stable IDs make reconnect/resume safe if the acknowledgement was lost.
    await rpc.rpc("orchestration.dispatchCommand", {
      type: "message.dispatch",
      commandId: row.commands.message,
      threadId: row.threadId,
      messageId: row.commands.messageId,
      text:
        task.publicFiles["TASK.md"] +
        "\nImplement the complete specification, including edge cases. Keep your final response short. If acting as advisor, specify solution.mjs as the only editable file and node --test public.test.mjs as the acceptance command.",
      attachments: [],
      createdBy: "user",
      creationSource: "web",
      modelSelection: group.worker,
      dispatchMode: { type: "start_immediately" },
    });
    const remaining = protocol.trialTimeoutMs - (Date.now() - Date.parse(row.startedAt));
    try {
      await rpc.waitFor(row.threadId, (state) => done(row, state), Math.max(1, remaining));
    } catch (error) {
      await stop(row, error.message);
      await rpc.waitFor(row.threadId, (state) => state.runs.every(terminal), 60_000);
    }
    const projection = await rpc.projection(row.threadId);
    const turns = (await usage()).get(row.threadId) ?? [];
    const score = await evaluate(task, row.cwd);
    const toolCalls = projection.nodes.filter((node) => node.kind === "tool_call").length;
    const completion =
      row.group === "B" ? projection.thread.advisorState?.phase : projection.runs.at(-1)?.status;
    const result = {
      ...row,
      completedAt: new Date().toISOString(),
      durationMs: Date.now() - Date.parse(row.startedAt),
      completion,
      toolCalls,
      passed: !row.stopReason && completion === "completed" && toolCalls > 0 && score.passed,
      score,
      scoringRevision: protocol.scoringRevision ?? 1,
      costUsd: accountedCost(turns),
      turns,
      runs: projection.runs.map((run) => ({
        id: run.id,
        status: run.status,
        usageRole: run.usageRole,
        model: run.modelSelection.model,
        providerThreadId: run.providerThreadId,
        advisorCycle: run.advisorCycle,
      })),
      handoffCount: projection.contextHandoffs.length,
      cycles: projection.thread.advisorState?.cycle ?? 0,
    };
    await atomic(row.id + ".projection.json", projection);
    await atomic(row.id + ".result.json", result);
    results.set(row.id, result);
    log({ id: row.id, passed: result.passed, costUsd: result.costUsd, toolCalls, completion });
    if (result.costUsd === null) throw Error("Missing or incomplete cost accounting for " + row.id);
  } catch (error) {
    if (row.startedAt) await stop(row, "trial_error: " + error.message).catch(() => {});
    throw error;
  } finally {
    active.delete(row.id);
  }
}

try {
  const manifest = { ...protocol, schedule: matrix, cold: coldTasks.map((t) => t.id) };
  if (await exists("manifest.json")) {
    if (hash(JSON.stringify(await read("manifest.json"))) !== hash(JSON.stringify(manifest)))
      throw Error("Frozen protocol changed; use a new output directory");
  } else {
    await atomic("manifest.json", manifest);
  }
  for (const file of await NodeFSP.readdir(out)) {
    if (file.endsWith(".result.json")) {
      const row = await read(file);
      results.set(row.id, row);
    }
  }
  if ([...results.values()].some((row) => row.costUsd === null))
    throw Error(
      "Resolve missing cost accounting before resuming; never treat unknown spend as zero",
    );
  if (values.phase === "matrix" && coldTasks.some((t) => !results.get("cold-" + t.id)?.passed))
    throw Error("Both cold starts must pass before matrix runs");
  await NodeFSP.mkdir(workspaces, { recursive: true });
  const session = JSON.parse(await NodeFSP.readFile(values.session, "utf8"));
  rpc = await new T3Rpc(values.url, session.token).connect();
  const config = await rpc.rpc("server.getConfig", {});
  const provider = config.providers.find((p) => p.instanceId === weak.instanceId);
  if (!provider?.enabled || provider.auth.status !== "authenticated")
    throw Error(weak.instanceId + " is not enabled and authenticated");
  for (const model of [protocol.groups.A.worker.model, weak.model])
    if (!provider.models.some((m) => m.slug === model)) throw Error("Model unavailable: " + model);
  await atomic("provider-preflight.json", {
    checkedAt: new Date().toISOString(),
    driver: provider.driver,
    version: provider.version,
    status: provider.status,
    models: provider.models.map((m) => m.slug),
    usageLimits: provider.usageLimits,
  });
  const refreshProgress = async () => {
    if (!active.size || monitor.busy) return;
    monitor.busy = true;
    try {
      const byThread = await usage();
      let total = [...results.values()].reduce((sum, r) => sum + r.costUsd, 0);
      for (const row of active.values()) {
        const turns = byThread.get(row.threadId) ?? [];
        const cost = turns.reduce((sum, t) => sum + (t.costUsd ?? 0), 0);
        total += cost;
        if (cost >= protocol.trialStopAtUsd) await stop(row, "trial_cost_guard");
      }
      await atomic("progress.json", {
        at: new Date().toISOString(),
        recorded: results.size,
        active: [...active.values()].map((r) => ({ id: r.id, threadId: r.threadId })),
        observedCostUsd: total,
        completeCostCoverage: [...results.values()].every((r) => r.costUsd !== null),
      });
      if (total >= protocol.stopLaunchingAtUsd) {
        shutdown = true;
        await Promise.all([...active.values()].map((row) => stop(row, "global_cost_guard")));
      }
    } catch (error) {
      monitorError = error;
      shutdown = true;
      log({ error: "Usage monitor failed; stopping owned trials", detail: error.message });
      await Promise.allSettled(
        [...active.values()].map((row) => stop(row, "usage_monitor_failed")),
      );
    } finally {
      monitor.busy = false;
    }
  };
  const monitor = setInterval(() => {
    if (!monitor.busy) monitorPending = refreshProgress();
  }, 10_000);
  const signalHandler = () => {
    shutdown = true;
    void Promise.allSettled([...active.values()].map((row) => stop(row, "runner_interrupted")));
  };
  process.on("SIGINT", signalHandler);
  process.on("SIGTERM", signalHandler);
  let entries =
    values.phase === "cold"
      ? coldTasks.map((task) => ({
          id: "cold-" + task.id,
          group: "cold",
          taskId: task.id,
          repetition: 0,
        }))
      : matrix;
  entries = entries.filter((entry) => !results.has(entry.id));
  if (values.take) {
    entries = entries.slice(0, Number(values.take));
  }
  let next = 0;
  async function worker() {
    while (!shutdown && next < entries.length) {
      const total = [...results.values()].reduce((sum, r) => sum + r.costUsd, 0);
      if (total >= protocol.stopLaunchingAtUsd) {
        shutdown = true;
        break;
      }
      const row = entries[next++];
      try {
        await runTrial(row);
      } catch (error) {
        shutdown = true;
        log({ id: row.id, error: error.message });
        await atomic("runner-error.json", {
          at: new Date().toISOString(),
          id: row.id,
          message: error.message,
        });
        await Promise.allSettled([...active.values()].map((row) => stop(row, "runner_error")));
      }
    }
  }
  try {
    await Promise.all(
      Array.from({ length: values.phase === "cold" ? 1 : protocol.concurrency }, worker),
    );
    if (monitorError) throw monitorError;
  } finally {
    clearInterval(monitor);
    await monitorPending;
    process.off("SIGINT", signalHandler);
    process.off("SIGTERM", signalHandler);
  }
  const totalCostUsd = [...results.values()].some((row) => row.costUsd === null)
    ? null
    : [...results.values()].reduce((sum, row) => sum + row.costUsd, 0);
  await atomic("progress.json", {
    at: new Date().toISOString(),
    finished: !shutdown,
    recorded: results.size,
    active: [],
    observedCostUsd: totalCostUsd,
    completeCostCoverage: totalCostUsd !== null,
  });
  log({
    phase: values.phase,
    finished: !shutdown,
    recorded: results.size,
    totalCostUsd,
  });
  if (shutdown) process.exitCode = 1;
} finally {
  rpc?.close();
  await lock.close();
  await NodeFSP.rm(NodePath.join(out, "runner.lock"), { force: true });
}
