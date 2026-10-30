import { assert, describe, it } from "@effect/vitest";
import { UsageDay } from "@t3tools/contracts";
import type { UsageRecord } from "@t3tools/provider-core/server/usage";
import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import * as SqlClient from "effect/sql/SqlClient";
import * as Tracer from "effect/Tracer";
import * as SqlitePersistence from "../persistence/Sqlite.ts";
import { accountTurn, make, type TurnAccountingRow } from "./ThreadUsageService.ts";
import { bundledRates } from "./bundledPricing.ts";

const row: TurnAccountingRow = {
  turnId: "turn-1",
  threadId: "thread-1",
  provider: "codex",
  driver: "codex",
  model: "gpt-6-luna",
  role: null,
  recordedAt: "2026-10-12T12:00:05Z",
  startedAt: "2026-10-12T12:00:00Z",
  completedAt: "2026-10-12T12:00:05Z",
  nativeSessionId: "native-1",
  usageJson: JSON.stringify({
    usageScope: "main_agent",
    usageStatus: "complete",
    inputTokens: 1000,
    cachedInputTokens: 700,
    outputTokens: 100,
    reasoningTokens: 25,
    hasSubagents: false,
  }),
};
const window = {
  timeZone: "UTC",
  sinceDay: UsageDay.make("2026-10-12"),
  untilDay: UsageDay.make("2026-10-12"),
};
const input = {
  window,
  records: [],
  rates: bundledRates,
  overrides: new Map(),
  pricingVersion: "fixture",
};
const encodeJson = Schema.encodeSync(Schema.fromJsonString(Schema.Unknown));

describe("turn accounting", () => {
  it("subtracts cached input once and never bills reasoning twice", () => {
    const turn = accountTurn(row, [], bundledRates, new Map(), "fixture");
    assert.equal(turn.inputTokens, 1000);
    assert.equal(turn.cachedInputTokens, 700);
    assert.equal(turn.outputTokens, 100);
    assert.equal(turn.reasoningTokens, 25);
    assert.equal(turn.role, "worker");
    assert.approximately(turn.costUsd!, 0.000087, 1e-12);
  });
  it("prices Haiku requests independently instead of treating a turn's aggregate as one long prompt", () => {
    const claude = {
      ...row,
      driver: "claudeAgent",
      provider: "claudeAgent",
      model: "claude-haiku-5-5",
      role: "advisor",
      usageJson: JSON.stringify({
        usageScope: "main_agent",
        usageStatus: "complete",
        inputTokens: 120000,
        outputTokens: 200,
        hasSubagents: false,
      }),
    };
    const records: UsageRecord[] = [0, 1].map((n) => ({
      provider: "claude",
      sessionId: "native-1",
      model: "claude-haiku-5-5",
      timestampMs: Date.parse(`2026-10-12T12:00:0${n + 1}Z`),
      totals: {
        uncachedInputTokens: 60000,
        cachedInputTokens: 0,
        cacheCreationTokens: 0,
        outputTokens: 100,
        reasoningTokens: 0,
      },
      reportedCostUsd: null,
      speed: "standard",
      dedupeKey: `msg-${n}`,
    }));
    const turn = accountTurn(claude, records, bundledRates, new Map(), "fixture");
    assert.equal(turn.role, "advisor");
    assert.approximately(turn.costUsd!, 0.0121, 1e-12);
  });
  it("prices native one-hour cache writes in the turn ledger", () => {
    const claude = {
      ...row,
      driver: "claudeAgent",
      provider: "claudeAgent",
      model: "claude-opus-5-5",
      usageJson: JSON.stringify({
        usageScope: "main_agent",
        usageStatus: "complete",
        inputTokens: 10000,
        cacheCreationTokens: 10000,
        outputTokens: 100,
        hasSubagents: false,
      }),
    };
    const native: UsageRecord = {
      provider: "claude",
      model: claude.model,
      sessionId: "native-1",
      timestampMs: Date.parse("2026-10-12T12:00:03Z"),
      totals: {
        uncachedInputTokens: 0,
        cachedInputTokens: 0,
        cacheCreationTokens: 10000,
        outputTokens: 100,
        reasoningTokens: 0,
      },
      cacheCreation1hTokens: 4000,
      speed: "standard",
      reportedCostUsd: null,
      dedupeKey: "msg-ttl",
    };
    assert.approximately(
      accountTurn(claude, [native], bundledRates, new Map(), "ttl").costUsd!,
      0.064,
      1e-12,
    );
  });
  it("prices a reported aggregate at base Haiku rates and one long request at long-context rates", () => {
    const haiku = (inputTokens: number, outputTokens: number) => ({
      ...row,
      driver: "claudeAgent",
      provider: "claudeAgent",
      model: "claude-haiku-5-5",
      usageJson: JSON.stringify({
        usageScope: "main_agent",
        usageStatus: "complete",
        inputTokens,
        outputTokens,
        hasSubagents: false,
      }),
    });
    // Twenty 25k-token requests, known only by the turn's total: each prompt is under 100k.
    const aggregate = accountTurn(haiku(500_000, 2_000), [], bundledRates, new Map(), "fixture");
    assert.approximately(aggregate.costUsd!, 500_000 * 0.1e-6 + 2_000 * 0.5e-6, 1e-12);
    const long: UsageRecord = {
      provider: "claude",
      sessionId: "native-1",
      model: "claude-haiku-5-5",
      timestampMs: Date.parse("2026-10-12T12:00:03Z"),
      totals: {
        uncachedInputTokens: 150_000,
        cachedInputTokens: 0,
        cacheCreationTokens: 0,
        outputTokens: 100,
        reasoningTokens: 0,
      },
      reportedCostUsd: null,
      speed: "standard",
      dedupeKey: "long",
    };
    const single = accountTurn(haiku(150_000, 100), [long], bundledRates, new Map(), "fixture");
    assert.approximately(single.costUsd!, 150_000 * 0.5e-6 + 100 * 2.5e-6, 1e-12);
  });
  it("prices an interrupted turn from its native requests and keeps it partial", () => {
    const partial = {
      ...row,
      usageJson: JSON.stringify({
        usageScope: "main_agent",
        usageStatus: "partial",
        inputTokens: 400,
        outputTokens: 0,
        hasSubagents: false,
      }),
    };
    const native: UsageRecord = {
      provider: "codex",
      sessionId: "native-1",
      model: row.model,
      timestampMs: Date.parse("2026-10-12T12:00:03Z"),
      totals: {
        uncachedInputTokens: 300,
        cachedInputTokens: 700,
        cacheCreationTokens: 0,
        outputTokens: 100,
        reasoningTokens: 25,
      },
      speed: "standard",
      reportedCostUsd: null,
      dedupeKey: "interrupted",
    };
    const turn = accountTurn(partial, [native], bundledRates, new Map(), "fixture");
    assert.equal(turn.usageStatus, "partial");
    assert.equal(turn.accountingScope, "mainAgent");
    assert.equal(turn.inputTokens, 1000);
    assert.equal(turn.outputTokens, 100);
    assert.equal(turn.costSource, "modelPriced");
    assert.approximately(turn.costUsd!, 0.000087, 1e-12);
  });
  it("keeps missing and partial usage visibly unpriced", () => {
    assert.equal(
      accountTurn({ ...row, usageJson: null }, [], bundledRates, new Map(), "fixture").costUsd,
      null,
    );
    const partial = {
      ...row,
      usageJson: JSON.stringify({
        usageScope: "main_agent",
        usageStatus: "partial",
        inputTokens: 1000,
        hasSubagents: false,
      }),
    };
    assert.equal(
      accountTurn(partial, [], bundledRates, new Map(), "fixture").usageStatus,
      "partial",
    );
    assert.equal(accountTurn(partial, [], bundledRates, new Map(), "fixture").costUsd, null);
  });
  it.effect(
    "persists unique turns across refreshes and service restarts, with role and historical model retained",
    () =>
      Effect.gen(function* () {
        const sql = yield* SqlClient.SqlClient;
        yield* sql`INSERT INTO orchestration_v2_projection_threads (thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, created_at, updated_at, payload_json) VALUES ('thread-1', 'project-1', 'Example', 'codex', 'full-access', 'default', '2026-10-12', '2026-10-12', '{"title":"Example"}')`;
        yield* sql`INSERT INTO orchestration_v2_projection_provider_threads (provider_thread_id, thread_id, provider, status, updated_at, payload_json) VALUES ('pt-1', 'thread-1', 'codex', 'idle', '2026-10-12', '{"providerInstanceId":"codex","nativeThreadRef":{"nativeId":"native-1"}}')`;
        for (const [n, role] of ["advisor", "worker"].entries()) {
          const run = `run-${n}`;
          const attempt = `attempt-${n}`;
          const turn = `turn-${n}`;
          yield* sql`INSERT INTO orchestration_v2_projection_runs (run_id, thread_id, ordinal, provider, status, requested_at, payload_json) VALUES (${run}, 'thread-1', ${n + 1}, 'codex', 'completed', '2026-10-12', ${encodeJson({ modelSelection: { model: row.model }, usageRole: role })})`;
          yield* sql`INSERT INTO orchestration_v2_projection_run_attempts (attempt_id, thread_id, run_id, attempt_ordinal, root_node_id, provider, provider_thread_id, status, payload_json) VALUES (${attempt}, 'thread-1', ${run}, 1, 'node', 'codex', 'pt-1', 'completed', '{}')`;
          yield* sql`INSERT INTO orchestration_v2_projection_provider_turns (provider_turn_id, thread_id, provider_thread_id, node_id, run_attempt_id, ordinal, status, started_at, completed_at, payload_json) VALUES (${turn}, 'thread-1', 'pt-1', 'node', ${attempt}, ${n + 1}, 'completed', ${row.startedAt}, ${row.completedAt}, ${`{"turnTokenUsage":${row.usageJson}}`})`;
        }
        const service = yield* make;
        const first = yield* service.read(input);
        assert.equal(first.threads[0]?.turns.length, 2);
        assert.deepEqual(
          first.threads[0]?.turns.map((turn) => turn.role),
          ["advisor", "worker"],
        );
        assert.deepEqual(yield* service.read(input), first);
        assert.equal((yield* sql`SELECT * FROM usage_turns`).length, 2);
        const restarted = yield* make;
        assert.deepEqual(yield* restarted.read(input), first);
        const empty = yield* restarted.read({
          ...input,
          window: {
            ...window,
            sinceDay: UsageDay.make("2026-10-13"),
            untilDay: UsageDay.make("2026-10-13"),
          },
        });
        assert.deepEqual(empty, { threads: [], deletedThreadTurns: [] });
        assert.equal((yield* sql`SELECT * FROM usage_turns`).length, 2);
      }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
  );
  it.effect("counts a sidekick's turns toward its lead's thread", () =>
    Effect.gen(function* () {
      const sql = yield* SqlClient.SqlClient;
      yield* sql`INSERT INTO orchestration_v2_projection_threads (thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, created_at, updated_at, payload_json) VALUES ('lead-1', 'project-1', 'Lead', 'codex', 'full-access', 'default', '2026-10-12', '2026-10-12', '{"title":"Lead","lineage":{"parentThreadId":null}}')`;
      yield* sql`INSERT INTO orchestration_v2_projection_threads (thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, created_at, updated_at, payload_json) VALUES ('side-1', 'project-1', 'Sidekick', 'codex', 'full-access', 'default', '2026-10-12', '2026-10-11', '{"title":"Sidekick","lineage":{"parentThreadId":"lead-1"}}')`;
      for (const [n, [thread, role]] of [
        ["lead-1", "lead"],
        ["side-1", "sidekick"],
      ].entries()) {
        const pt = `pt-${n}`;
        const run = `run-${n}`;
        const attempt = `attempt-${n}`;
        yield* sql`INSERT INTO orchestration_v2_projection_provider_threads (provider_thread_id, thread_id, provider, status, updated_at, payload_json) VALUES (${pt}, ${thread}, 'codex', 'idle', '2026-10-12', ${encodeJson({ providerInstanceId: "codex", nativeThreadRef: { nativeId: `native-${n}` } })})`;
        yield* sql`INSERT INTO orchestration_v2_projection_runs (run_id, thread_id, ordinal, provider, status, requested_at, payload_json) VALUES (${run}, ${thread}, 1, 'codex', 'completed', '2026-10-12', ${encodeJson({ modelSelection: { model: row.model }, usageRole: role })})`;
        yield* sql`INSERT INTO orchestration_v2_projection_run_attempts (attempt_id, thread_id, run_id, attempt_ordinal, root_node_id, provider, provider_thread_id, status, payload_json) VALUES (${attempt}, ${thread}, ${run}, 1, 'node', 'codex', ${pt}, 'completed', '{}')`;
        yield* sql`INSERT INTO orchestration_v2_projection_provider_turns (provider_turn_id, thread_id, provider_thread_id, node_id, run_attempt_id, ordinal, status, started_at, completed_at, payload_json) VALUES (${`turn-${n}`}, ${thread}, ${pt}, 'node', ${attempt}, 1, 'completed', ${row.startedAt}, ${row.completedAt}, ${`{"turnTokenUsage":${row.usageJson}}`})`;
      }
      const { threads } = yield* (yield* make).read(input);
      const lead = threads.find((thread) => thread.threadId === "lead-1");
      const sidekick = threads.find((thread) => thread.threadId === "side-1");
      assert.deepEqual(
        lead?.turns.map((turn) => [turn.role, turn.threadId]),
        [
          ["lead", "lead-1"],
          ["sidekick", "side-1"],
        ],
      );
      // The sidekick thread has no turns of its own to list.
      assert.isUndefined(sidekick);
    }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
  );
  it.effect(
    "includes Claude CLI auxiliary usage and preserves it after cleanup, restart and repricing",
    () =>
      Effect.gen(function* () {
        const sql = yield* SqlClient.SqlClient;
        yield* sql`INSERT INTO orchestration_v2_projection_threads (thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, created_at, updated_at, payload_json) VALUES ('thread-1', 'project-1', 'Example', 'claudeAgent', 'full-access', 'default', '2026-10-12', '2026-10-12', '{"title":"Example"}')`;
        yield* sql`INSERT INTO orchestration_v2_projection_provider_threads (provider_thread_id, thread_id, provider, status, updated_at, payload_json) VALUES ('pt-1', 'thread-1', 'claudeAgent', 'idle', '2026-10-12', '{"providerInstanceId":"claude-custom","nativeThreadRef":{"nativeId":"native-1"}}')`;
        yield* sql`INSERT INTO orchestration_v2_projection_runs (run_id, thread_id, ordinal, provider, status, requested_at, payload_json) VALUES ('run-1', 'thread-1', 1, 'claudeAgent', 'completed', '2026-10-12', '{"modelSelection":{"model":"claude-haiku-5-5"},"usageRole":"advisor"}')`;
        yield* sql`INSERT INTO orchestration_v2_projection_run_attempts (attempt_id, thread_id, run_id, attempt_ordinal, root_node_id, provider, provider_thread_id, status, payload_json) VALUES ('attempt-1', 'thread-1', 'run-1', 1, 'node', 'claudeAgent', 'pt-1', 'completed', '{}')`;
        yield* sql`INSERT INTO orchestration_v2_projection_provider_turns (provider_turn_id, thread_id, provider_thread_id, node_id, run_attempt_id, ordinal, status, started_at, completed_at, payload_json) VALUES ('turn-1', 'thread-1', 'pt-1', 'node', 'attempt-1', 1, 'completed', ${row.startedAt}, ${row.completedAt}, ${`{"turnTokenUsage":${row.usageJson}}`})`;
        const billing: UsageRecord = {
          provider: "claude",
          sessionId: "native-1",
          model: "claude-haiku-5-5",
          timestampMs: Date.parse("2026-10-12T12:00:03Z"),
          totals: {
            uncachedInputTokens: 320,
            cachedInputTokens: 700,
            cacheCreationTokens: 0,
            outputTokens: 103,
            reasoningTokens: 25,
          },
          speed: "standard",
          reportedCostUsd: 0.2,
          dedupeKey: "snapshot-1",
        };
        const service = yield* make;
        assert.deepEqual([...(yield* service.claudeSessionIds)], ["native-1"]);
        const first = yield* service.read({ ...input, claudeSessionRecords: [billing] });
        const turn = first.threads[0]!.turns[0]!;
        assert.equal(turn.inputTokens, 1020);
        assert.equal(turn.outputTokens, 103);
        assert.equal(turn.driver, "claude");
        assert.equal(turn.accountingScope, "sessionWithAuxiliary");
        assert.equal(turn.costUsd, 0.2);
        assert.deepEqual(yield* service.read({ ...input, claudeSessionRecords: [billing] }), first);
        const restarted = yield* make;
        assert.deepEqual(yield* restarted.read(input), first);
        assert.equal((yield* sql`SELECT * FROM usage_turns`).length, 1);
        const overrides = new Map([
          [
            "claude-haiku-5-5",
            {
              inputCostPerToken: 0.001,
              cacheReadCostPerToken: 0.001,
              cacheCreationCostPerToken: 0.001,
              outputCostPerToken: 0.002,
              fast: null,
              ultrafast: null,
            },
          ],
        ]);
        const repriced = yield* restarted.read({
          ...input,
          overrides,
          pricingVersion: "overrides",
        });
        assert.approximately(repriced.threads[0]!.turns[0]!.costUsd!, 1.226, 1e-12);
        assert.equal(repriced.threads[0]!.turns[0]!.inputTokens, 1020);
      }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
  );
  it.effect("counts a Claude session snapshot once when one CLI process served several turns", () =>
    Effect.gen(function* () {
      const sql = yield* SqlClient.SqlClient;
      yield* sql`INSERT INTO orchestration_v2_projection_threads (thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, created_at, updated_at, payload_json) VALUES ('thread-1', 'project-1', 'Example', 'claudeAgent', 'full-access', 'default', '2026-10-12', '2026-10-12', '{"title":"Example"}')`;
      yield* sql`INSERT INTO orchestration_v2_projection_provider_threads (provider_thread_id, thread_id, provider, status, updated_at, payload_json) VALUES ('pt-1', 'thread-1', 'claudeAgent', 'idle', '2026-10-12', '{"providerInstanceId":"claudeAgent","nativeThreadRef":{"nativeId":"native-1"}}')`;
      // Turns 1 and 2 share one CLI process; turn 3 runs in a resumed one.
      for (const n of [1, 2, 3]) {
        yield* sql`INSERT INTO orchestration_v2_projection_runs (run_id, thread_id, ordinal, provider, status, requested_at, payload_json) VALUES (${`run-${n}`}, 'thread-1', ${n}, 'claudeAgent', 'completed', '2026-10-12', '{"modelSelection":{"model":"claude-haiku-5-5"}}')`;
        yield* sql`INSERT INTO orchestration_v2_projection_run_attempts (attempt_id, thread_id, run_id, attempt_ordinal, root_node_id, provider, provider_thread_id, status, payload_json) VALUES (${`attempt-${n}`}, 'thread-1', ${`run-${n}`}, 1, 'node', 'claudeAgent', 'pt-1', 'completed', '{}')`;
        yield* sql`INSERT INTO orchestration_v2_projection_provider_turns (provider_turn_id, thread_id, provider_thread_id, node_id, run_attempt_id, ordinal, status, started_at, completed_at, payload_json) VALUES (${`turn-${n}`}, 'thread-1', 'pt-1', 'node', ${`attempt-${n}`}, ${n}, 'completed', ${`2026-10-12T12:00:${n}0Z`}, ${`2026-10-12T12:00:${n}5Z`}, ${`{"turnTokenUsage":${row.usageJson}}`})`;
      }
      // Claude writes its cumulative cost-state when the process exits, timed by the last reply.
      const snapshot = (second: number, turns: number, cost: number) =>
        ({
          provider: "claude",
          sessionId: "native-1",
          model: "claude-haiku-5-5",
          timestampMs: Date.parse(`2026-10-12T12:00:${second}Z`),
          totals: {
            uncachedInputTokens: 300 * turns + 20,
            cachedInputTokens: 700 * turns,
            cacheCreationTokens: 0,
            outputTokens: 100 * turns + 3,
            reasoningTokens: 25 * turns,
          },
          speed: "standard",
          reportedCostUsd: cost,
          dedupeKey: `snapshot-${second}`,
        }) satisfies UsageRecord;
      const records = [snapshot(23, 2, 0.3), snapshot(33, 1, 0.2)];
      const service = yield* make;
      const first = yield* service.read({ ...input, claudeSessionRecords: records });
      assert.deepEqual(
        first.threads[0]!.turns.map((turn) => [
          turn.turnId,
          turn.inputTokens,
          turn.accountingScope,
        ]),
        [
          ["turn-1", 1000, "mainAgent"],
          ["turn-2", 1000, "mainAgent"],
          ["turn-3", 1020, "sessionWithAuxiliary"],
        ],
      );
      assert.equal(first.threads[0]!.turns[2]!.costUsd, 0.2);
      // After transcript cleanup the ledger keeps the same accounting.
      assert.deepEqual(yield* service.read(input), first);
    }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
  );
});

const seedWindowTurns = Effect.gen(function* () {
  const sql = yield* SqlClient.SqlClient;
  yield* sql`INSERT INTO orchestration_v2_projection_threads (thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, created_at, updated_at, payload_json) VALUES ('thread-1', 'project-1', 'Example', 'codex', 'full-access', 'default', '2026-10-12', '2026-10-12', '{"title":"Example"}')`;
  yield* sql`INSERT INTO orchestration_v2_projection_provider_threads (provider_thread_id, thread_id, provider, status, updated_at, payload_json) VALUES ('pt-1', 'thread-1', 'codex', 'idle', '2026-10-12', '{"providerInstanceId":"codex","nativeThreadRef":{"nativeId":"native-1"},"nativeMetadata":{"modelSelection":{"model":"gpt-6-luna"}}}')`;
  yield* sql`INSERT INTO orchestration_v2_projection_provider_turns (provider_turn_id, thread_id, provider_thread_id, node_id, ordinal, status, started_at, completed_at, payload_json) VALUES ('turn-1', 'thread-1', 'pt-1', 'node', 1, 'completed', ${row.startedAt}, ${row.completedAt}, ${`{"turnTokenUsage":${row.usageJson}}`})`;
});

it.effect(
  "bounds source joins and ledger decoding to the window, and only rewrites changed inputs",
  () =>
    Effect.gen(function* () {
      yield* seedWindowTurns;
      const sql = yield* SqlClient.SqlClient;
      // These old payloads must never be read, decoded, or repriced for this window.
      yield* sql`INSERT INTO usage_turns VALUES ('old', 'thread-1', '2020-01-01', 'invalid-json')`;
      yield* sql`INSERT INTO orchestration_v2_projection_provider_turns (provider_turn_id, thread_id, provider_thread_id, node_id, ordinal, status, completed_at, payload_json) VALUES ('old', 'thread-1', 'pt-1', 'node-old', 2, 'completed', '2020-01-01', 'invalid-json')`;
      const service = yield* make;
      const first = yield* service.read(input);
      assert.equal(first.threads[0]?.turns.length, 1);
      const writes: string[] = [];
      const tracer = Tracer.make({
        span(options) {
          const span = new Tracer.NativeSpan(options);
          const end = span.end.bind(span);
          span.end = (time, exit) => {
            end(time, exit);
            const query = span.attributes.get("db.query.text");
            if (typeof query === "string" && query.includes("INSERT INTO usage_turns"))
              writes.push(query);
          };
          return span;
        },
      });
      assert.deepEqual(yield* service.read(input).pipe(Effect.withTracer(tracer)), first);
      assert.equal(writes.length, 0);
      const changed = { ...JSON.parse(row.usageJson!), outputTokens: 200 };
      yield* sql`UPDATE orchestration_v2_projection_provider_turns SET payload_json = ${encodeJson({ turnTokenUsage: changed })} WHERE provider_turn_id = 'turn-1'`;
      const updated = yield* service.read(input).pipe(Effect.withTracer(tracer));
      assert.equal(updated.threads[0]?.turns[0]?.outputTokens, 200);
      assert.equal(writes.length, 1);
      // The fingerprint includes rates even if the caller reuses a version string.
      const overrides = new Map([
        [
          row.model,
          {
            inputCostPerToken: 1,
            cacheReadCostPerToken: 1,
            cacheCreationCostPerToken: 1,
            outputCostPerToken: 1,
            fast: null,
            ultrafast: null,
          },
        ],
      ]);
      const repriced = yield* service.read({ ...input, overrides });
      assert.equal(repriced.threads[0]?.turns[0]?.costUsd, 1200);
      assert.notProperty(repriced.threads[0]!.turns[0]!, "inputFingerprint");
      // A corrected timestamp must also evict the stale ledger result from the old window.
      yield* sql`UPDATE orchestration_v2_projection_provider_turns SET completed_at = '2026-10-15T00:00:00Z' WHERE provider_turn_id = 'turn-1'`;
      assert.equal((yield* service.read(input)).threads.length, 0);
      const queryPlan = yield* sql<{ detail: string }>`EXPLAIN QUERY PLAN
      SELECT provider_turn_id FROM orchestration_v2_projection_provider_turns
      WHERE julianday(COALESCE(completed_at, started_at)) >= julianday('2026-10-12')
        AND julianday(COALESCE(completed_at, started_at)) < julianday('2026-10-13')`;
      assert.include(
        queryPlan.map((entry) => entry.detail).join(" "),
        "provider_turns_usage_window_idx",
      );
    }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
);

it.effect.each([false, true])(
  "keeps cross-hour request accounting across partial scans (anonymous: %s)",
  (anonymous) =>
    Effect.gen(function* () {
      yield* seedWindowTurns;
      const sql = yield* SqlClient.SqlClient;
      yield* sql`UPDATE orchestration_v2_projection_provider_turns SET started_at = '2026-10-12T11:59:50Z', completed_at = '2026-10-12T12:00:10Z'`;
      const records: UsageRecord[] = ["2026-10-12T11:59:55Z", "2026-10-12T12:00:05Z"].map(
        (at, i) => ({
          provider: "codex",
          sessionId: "native-1",
          model: row.model,
          timestampMs: Date.parse(at),
          totals: {
            uncachedInputTokens: 150,
            cachedInputTokens: 350,
            cacheCreationTokens: 0,
            outputTokens: 50,
            reasoningTokens: 0,
          },
          speed: "standard",
          reportedCostUsd: i === 0 ? 0.2 : 0.3,
          dedupeKey: anonymous ? null : `request-${i}`,
        }),
      );
      const service = yield* make;
      const full = yield* service.read({ ...input, records });
      assert.equal(full.threads[0]!.turns[0]!.costUsd, 0.5);
      const narrow = {
        ...window,
        resolution: "hour" as const,
        sinceTime: "2026-10-12T12:00:00Z",
        untilTime: "2026-10-12T13:00:00Z",
      };
      const narrowed = yield* service.read({
        ...input,
        window: narrow,
        // Codex gets a copy-dedupe key only when a scan sees a second transcript.
        records: [{ ...records[1]!, dedupeKey: "copied-session-request" }],
      });
      assert.equal(narrowed.threads[0]!.turns[0]!.costUsd, 0.5);
      const restarted = yield* make;
      assert.equal(
        (yield* restarted.read({ ...input, window: narrow })).threads[0]!.turns[0]!.costUsd,
        0.5,
      );
      // A corrected native price replaces the same request, never duplicates it.
      const corrected = yield* service.read({
        ...input,
        records: [{ ...records[1]!, reportedCostUsd: 0.4 }],
      });
      assert.approximately(corrected.threads[0]!.turns[0]!.costUsd!, 0.6, 1e-12);
    }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
);

it.effect("preserves repeated anonymous requests without multiplying them on refresh", () =>
  Effect.gen(function* () {
    yield* seedWindowTurns;
    const native: UsageRecord = {
      provider: "codex",
      sessionId: "native-1",
      model: row.model,
      timestampMs: Date.parse("2026-10-12T12:00:03Z"),
      totals: {
        uncachedInputTokens: 150,
        cachedInputTokens: 350,
        cacheCreationTokens: 0,
        outputTokens: 50,
        reasoningTokens: 0,
      },
      speed: "standard",
      reportedCostUsd: 0.25,
      dedupeKey: null,
    };
    const service = yield* make;
    for (const records of [[native, native], [native], [native, native]]) {
      assert.equal((yield* service.read({ ...input, records })).threads[0]!.turns[0]!.costUsd, 0.5);
    }
  }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
);

it.effect("includes late native prices and retains them after transcript cleanup", () =>
  Effect.gen(function* () {
    yield* seedWindowTurns;
    const service = yield* make;
    yield* service.read(input);
    const native: UsageRecord = {
      provider: "codex",
      sessionId: "native-1",
      model: row.model,
      timestampMs: Date.parse("2026-10-12T12:00:03Z"),
      totals: {
        uncachedInputTokens: 300,
        cachedInputTokens: 700,
        cacheCreationTokens: 0,
        outputTokens: 100,
        reasoningTokens: 25,
      },
      speed: "standard",
      reportedCostUsd: 0.5,
      dedupeKey: "late",
    };
    const priced = yield* service.read({ ...input, records: [native] });
    assert.equal(priced.threads[0]?.turns[0]?.costUsd, 0.5);
    const restarted = yield* make;
    assert.deepEqual(yield* restarted.read(input), priced);
  }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
);

it.effect("honors local DST days, hourly exclusive ends, and undated turns", () =>
  Effect.gen(function* () {
    yield* seedWindowTurns;
    const sql = yield* SqlClient.SqlClient;
    yield* sql`UPDATE orchestration_v2_projection_provider_turns SET started_at = '2026-11-01T05:00:00Z', completed_at = '2026-11-02T05:59:59Z'`;
    const service = yield* make;
    const day = {
      timeZone: "America/Chicago",
      sinceDay: UsageDay.make("2026-11-01"),
      untilDay: UsageDay.make("2026-11-01"),
    };
    assert.equal((yield* service.read({ ...input, window: day })).threads[0]?.turns.length, 1);
    yield* sql`UPDATE orchestration_v2_projection_provider_turns SET completed_at = '2026-11-02T06:00:00Z'`;
    assert.equal((yield* service.read({ ...input, window: day })).threads.length, 0);
    const hour = {
      ...day,
      resolution: "hour" as const,
      sinceTime: "2026-11-02T05:00:00Z",
      untilTime: "2026-11-02T06:00:00Z",
    };
    assert.equal((yield* service.read({ ...input, window: hour })).threads.length, 0);
    yield* sql`UPDATE orchestration_v2_projection_provider_turns SET completed_at = NULL, started_at = NULL`;
    yield* sql`UPDATE orchestration_v2_projection_threads SET updated_at = '2026-11-02T05:00:00Z'`;
    assert.equal((yield* service.read({ ...input, window: hour })).threads[0]?.turns.length, 1);
  }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
);

it.effect("skips an undecodable ledger row and re-accounts its turn", () =>
  Effect.gen(function* () {
    yield* seedWindowTurns;
    const sql = yield* SqlClient.SqlClient;
    const service = yield* make;
    const first = yield* service.read(input);
    yield* sql`UPDATE usage_turns SET payload_json = '{' WHERE turn_id = 'turn-1'`;
    yield* sql`INSERT INTO usage_turns VALUES ('ghost', 'thread-1', '2026-10-12T13:00:00Z', 'not-json')`;
    assert.deepEqual(yield* service.read(input), first);
    const healed = yield* sql<{ payload_json: string }>`
      SELECT payload_json FROM usage_turns WHERE turn_id = 'turn-1'
    `;
    assert.notEqual(healed[0]?.payload_json, "{");
  }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
);

it.effect("lists only threads with turns and keeps deleted threads' spend without titles", () =>
  Effect.gen(function* () {
    yield* seedWindowTurns;
    const sql = yield* SqlClient.SqlClient;
    yield* sql`INSERT INTO orchestration_v2_projection_threads (thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, created_at, updated_at, payload_json) VALUES ('idle', 'project-1', 'Idle', 'codex', 'full-access', 'default', '2026-10-12', '2026-10-12', '{"title":"Idle"}')`;
    yield* sql`INSERT INTO orchestration_v2_projection_threads (thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, created_at, updated_at, deleted_at, payload_json) VALUES ('gone', 'project-1', 'Gone', 'codex', 'full-access', 'default', '2026-10-12', '2026-10-12', '2026-10-12T13:00:00Z', '{"title":"Gone"}')`;
    yield* sql`INSERT INTO orchestration_v2_projection_provider_threads (provider_thread_id, thread_id, provider, status, updated_at, payload_json) VALUES ('pt-gone', 'gone', 'codex', 'idle', '2026-10-12', '{"providerInstanceId":"codex","nativeThreadRef":{"nativeId":"native-gone"},"nativeMetadata":{"modelSelection":{"model":"gpt-6-luna"}}}')`;
    yield* sql`INSERT INTO orchestration_v2_projection_provider_turns (provider_turn_id, thread_id, provider_thread_id, node_id, ordinal, status, started_at, completed_at, payload_json) VALUES ('turn-gone', 'gone', 'pt-gone', 'node', 1, 'completed', ${row.startedAt}, ${row.completedAt}, ${`{"turnTokenUsage":${row.usageJson}}`})`;
    const report = yield* (yield* make).read(input);
    assert.deepEqual(
      report.threads.map((thread) => [thread.threadId, thread.title, thread.turns.length]),
      [["thread-1", "Example", 1]],
    );
    assert.deepEqual(
      report.deletedThreadTurns.map((turn) => [turn.turnId, turn.costUsd]),
      [["turn-gone", report.threads[0]!.turns[0]!.costUsd]],
    );
  }).pipe(Effect.provide(SqlitePersistence.layerMemory)),
);
