import { tasks, coldTasks } from "./tasks.mjs";
import { hash } from "./evaluate.mjs";

const selection = (model) => ({
  instanceId: "claudeAgent",
  model,
  options: [
    { id: "effort", value: "medium" },
    { id: "fastMode", value: false },
  ],
});
const strong = selection("claude-opus-5-5");
const weak = selection("claude-haiku-5-5");
export const protocol = {
  version: 2,
  scoringRevision: 2,
  scoringErrata: [
    {
      taskId: "retry",
      reason:
        "The public task requires rejecting invalid numeric options but does not specify an exception subclass. Accept any Error, including RangeError, instead of requiring TypeError.",
      originalAssertion:
        "await assert.rejects(api.retry(()=>0,{...options,...override}),TypeError);",
      correctedAssertion: "await assert.rejects(api.retry(()=>0,{...options,...override}),Error);",
      policy:
        "Re-evaluate all saved solutions uniformly; preserve original outcomes; no model reruns or hidden-test feedback.",
    },
  ],
  purpose: "M4 subscription-model comparison; NVIDIA/Nebius evaluation deferred by user",
  groups: { A: { worker: strong }, B: { worker: weak, advisor: strong }, C: { worker: weak } },
  repetitions: 3,
  concurrency: 2,
  trialTimeoutMs: 600_000,
  stopLaunchingAtUsd: 45,
  trialStopAtUsd: 1.5,
  budgetUsd: 50,
  budgetNote:
    "API-equivalent spend guard, sampled every 10s; $5 global headroom for in-flight work. Subscription invoices are not API charges.",
  scoring:
    "All hidden and visible assertions pass, solution changes, public fixtures unchanged, successful terminal run/pipeline, and at least one observed tool call.",
  retries:
    "No external retry or hidden-test feedback. B uses the unchanged M3 public-acceptance pipeline (3 worker failures per replan, up to 3 replans).",
  prices: {
    source: "https://platform.claude.com/docs/en/about-claude/pricing",
    checkedOn: "2026-10-13",
    perMillionTokens: {
      "claude-opus-5-5": { input: 4, output: 20, cacheRead: 0.2, cacheWrite5m: 5, cacheWrite1h: 8 },
      "claude-haiku-5-5": {
        input: 0.1,
        output: 0.5,
        cacheRead: 0.01,
        cacheWrite5m: 0.125,
        cacheWrite1h: 0.2,
        longContextAbove100kMultiplier: 5,
      },
    },
    accounting:
      "T3 server.getUsageSummary; each turn retains its native accounting scope, provider/pricing source and version. Output already includes reasoning tokens. The audit independently recomputes main-agent prices per native request.",
  },
  suiteSha256: hash(JSON.stringify({ tasks, coldTasks })),
};

export function getProtocol(profile = "claude") {
  if (profile === "claude") return protocol;
  if (profile !== "gpt") throw Error("Unknown benchmark profile: " + profile);
  const selection = (model) => ({
    instanceId: "codex",
    model,
    options: [
      { id: "reasoningEffort", value: "medium" },
      { id: "serviceTier", value: "default" },
    ],
  });
  const strong = selection("gpt-6-astra"),
    weak = selection("gpt-6-luna");
  return {
    ...protocol,
    profile: "gpt",
    purpose: "M4 GPT Astra/Luna subscription-model comparison",
    groups: { A: { worker: strong }, B: { worker: weak, advisor: strong }, C: { worker: weak } },
    prices: {
      source: "https://developers.openai.com/api/docs/pricing",
      checkedOn: "2026-10-13",
      perMillionTokens: {
        "gpt-6-astra": { input: 10, cacheRead: 1, cacheWrite: 12.5, output: 50 },
        "gpt-6-luna": { input: 0.1, cacheRead: 0.01, cacheWrite: 0.125, output: 0.5 },
      },
      longContext: { aboveInputTokens: 272000, inputMultiplier: 2, outputMultiplier: 1.5 },
      accounting:
        "T3 turn ledger, independently reconciled to Codex native rollout total_token_usage and request-level standard API prices; reasoning is already included in output. Codex does not report a cash invoice or Claude-style auxiliary cost-state.",
    },
  };
}

export function schedule() {
  const rows = [];
  // Rotate group order to distribute warm-cache and time-of-run effects.
  for (let repetition = 1; repetition <= 3; repetition++) {
    for (let i = 0; i < tasks.length; i++) {
      const task = tasks[(i + (repetition - 1) * 4) % tasks.length];
      const groups = ["A", "B", "C"];
      const rotation = (i + repetition - 1) % 3;
      for (let j = 0; j < 3; j++) {
        const group = groups[(j + rotation) % 3];
        rows.push({
          id: group + "-" + task.id + "-" + repetition,
          group,
          taskId: task.id,
          repetition,
        });
      }
    }
  }
  return rows;
}

export function accountedCost(turns) {
  if (
    !turns.length ||
    turns.some(
      (t) =>
        t.usageStatus !== "complete" ||
        t.costUsd === null ||
        !Number.isFinite(t.costUsd) ||
        t.costUsd < 0,
    )
  )
    return null;
  return turns.reduce((sum, t) => sum + t.costUsd, 0);
}
