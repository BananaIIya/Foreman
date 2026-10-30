# M4 coding-agent comparison

Run 12 small JavaScript tasks, three repetitions each, through T3 Code's authenticated RPC:

| Group | Default (`--profile claude`)                      | `--profile gpt`                         |
| ----- | ------------------------------------------------- | --------------------------------------- |
| A     | Claude Opus 5.5 completes the task                | GPT-6 Astra completes the task          |
| B     | Claude Opus 5.5 advisor → Claude Haiku 5.5 worker | GPT-6 Astra advisor → GPT-6 Luna worker |
| C     | Claude Haiku 5.5 completes the task               | GPT-6 Luna completes the task           |

These configurations follow the request to defer NVIDIA models and use an existing subscription. They do **not** validate Nemotron or spend Nebius credits. The two cold-start tasks exercise the selected worker's tools and actual file edits. Both profiles use Medium reasoning and the standard service tier, with the same task suite, scoring and limits.

## Run

Use the development server's isolated T3 home, never the live installation's data directory. The selected provider (Claude Code or Codex) must already be enabled and authenticated. Put workspaces outside this repository so agents receive only the public task, starter implementation and visible smoke test. Reference implementations and hidden assertions stay in the evaluator.

From the repository root:

```sh
./node_modules/.bin/vp test run scripts/benchmark/*.test.mjs

# Keep this credential file in the ignored development home.
node apps/server/src/bin.ts auth session issue \
  --base-dir "$PWD/.t3" --label m4-experiment --ttl 24h --json \
  > "$PWD/.t3/m4-session.json"

M4_RESULTS="$PWD/../m4-results"
M4_WORKSPACES="$PWD/../m4-workspaces"

node scripts/benchmark/run.mjs --phase cold \
  --url http://127.0.0.1:13773 --out "$M4_RESULTS" \
  --workspaces "$M4_WORKSPACES" --session "$PWD/.t3/m4-session.json"

node scripts/benchmark/run.mjs --phase matrix \
  --url http://127.0.0.1:13773 --out "$M4_RESULTS" \
  --workspaces "$M4_WORKSPACES" --session "$PWD/.t3/m4-session.json"

# After all provider processes have exited, reconcile the final native cost-state.
node scripts/benchmark/audit.mjs "$M4_RESULTS" "$PWD/.t3/m4-session.json" \
  http://127.0.0.1:13773
node scripts/benchmark/analyze.mjs "$M4_RESULTS"
```

Use the actual port of your development server. Node 24 or newer is required. No additional npm dependencies are needed. Run the matrix in a persistent terminal if it must survive chat switches.

For Astra/Luna, add `--profile gpt` to **both** runner commands, use fresh result and workspace directories, and replace `audit.mjs` with `audit-codex.mjs`. The Codex audit reads matching native rollout files from `~/.codex/sessions`; pass a custom Codex home as the fourth argument when needed. It independently reconciles per-request deltas with final cumulative counters and T3's ledger, validates the selected models and options, and recomputes standard API prices per request. Reasoning tokens are included in output and must not be billed twice. Codex exposes main-agent accounting; Claude's complete native session cost includes auxiliary calls, so their aggregate totals have different scopes.

The optional --take 3 argument runs the next three unrecorded trials; these remain part of the same matrix. Repeating a command skips recorded results and uses the same RPC command IDs for unfinished trials. A lock prevents simultaneous runners. After a crash, inspect the PID in runner.lock, the local T3 threads and trial files before removing a stale lock. Do not delete evidence or rerun failed trials to improve the score.

The manifest freezes task content, models, options, ordering and limits. Use a new output/workspace directory when changing the experiment. Once finished, revoke the temporary session with the CLI's auth session revoke command and remove the credential file.

## Scoring and accounting

To measure worker-first routing, add `--strategy worker-first` to both cold and matrix commands. The runner explicitly defaults to `plan-first` to preserve the original experiment. `--groups B` runs just the 36 B trials with the same tasks and repetitions; use a fresh output directory and the same arguments for both phases. Its summary has no concurrent A/B comparison. Comparisons to published controls must be labeled historical. Worker options are persisted so automatic retries retain the selected effort and service tier.

Each trial uses a fresh Git repository and T3 thread. Group order rotates across tasks and repetitions. All groups receive the same public specification. B additionally uses the existing advisor plan and public-acceptance retry policy; this is a comparison of whole systems. Hidden failures are never returned to a model or retried.

A pass requires a successful final run/pipeline, observed tool use, a changed implementation, unchanged public fixtures and success on both visible and hidden assertions. The evaluator checks solutions in a separate, time-limited Node process. It validates its own oracles against reference and broken implementations before inference.

Every trial records timestamps, model/role runs, tool calls, solution hash, hidden-test outcome, token totals, native accounting scope, pricing version and API-equivalent cost. Unknown cost blocks further runs. The spend monitor stops launching near $45 and requests cancellation at $1.50 per trial, with $5 headroom below the $50 experiment budget. These are sampled guards, not a provider billing cap. Subscription usage is reported at API-equivalent prices and is not a cash invoice. The returned per-turn accounting scope identifies whether auxiliary native calls were available. Claude's final cost-state may arrive only after its process exits; the final audit is required because the live monitor can undercount that delayed usage. CLI auxiliary requests can use other models. Preserve them in whole-system totals and show main-message cost separately.

The analyzer writes summary.json, results.csv and portable results.json. It checks duplicates, the 108-trial matrix, cost-ledger consistency, and advisor/worker handoff evidence. A partial run never produces a final A/B comparison. Raw local projections remain in the results directory; portable exports omit workspace paths and message transcripts.

Treat these as small synthetic task results, not a general coding leaderboard. Repetitions of the same task are correlated. Keep failed cases, costs of retries and cold-start costs in the report. Nemotron's cold starts and A/B/C comparison remain a separate future experiment.

## Published results and scoring correction

The [October 12 GPT experiment](../../benchmarks/m4-gpt-2026-10-12/README.md) runs Astra/Luna with scoring revision 2 fixed before inference. Its portable evidence includes all 110 saved solutions and reconciliation of 146 native Codex sessions. Use the same offline verifier with that results directory.

The [October 12 Haiku experiment](../../benchmarks/m4-haiku-2026-10-12/README.md) includes portable results, original outcomes, final native accounting and all generated candidate implementations. Recheck its saved solutions without a provider or credential:

```sh
node scripts/benchmark/verify-results.mjs benchmarks/m4-haiku-2026-10-12
```

Scoring revision 2 fixes one hidden assertion for `retry`: the public contract requires rejecting invalid numeric options, but never specifies `TypeError` rather than `RangeError`. All 110 saved solutions were re-evaluated uniformly; seven original failures changed to passes. No model was rerun, no candidate changed, and no hidden-test feedback was sent to a model. The original manifest, aggregate scores and per-trial outcomes remain in the published evidence. `regrade.mjs RESULTS` applies this specific correction to a complete local experiment while preserving its original evaluation.
