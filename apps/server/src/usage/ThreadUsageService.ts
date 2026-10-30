// @effect-diagnostics globalDate:off - formatting persisted timestamps, never reading the clock.
// @effect-diagnostics nodeBuiltinImport:off -- Pure synchronous fingerprints avoid async crypto for each ledger row.
import * as NodeCrypto from "node:crypto";
import {
  TurnTokenUsage,
  UsageTurn,
  UsageReadError,
  UsageProviderKind,
  UsageTokenTotals,
  type UsageThread,
  type UsageSummaryInput,
} from "@t3tools/contracts";
import type { UsageRecord } from "@t3tools/provider-core/server/usage";
import * as Context from "effect/Context";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Option from "effect/Option";
import * as Schema from "effect/Schema";
import * as SqlClient from "effect/sql/SqlClient";
import { priceUsage, type RateTable } from "./usagePricing.ts";

export interface TurnAccountingRow {
  readonly turnId: string;
  readonly threadId: string;
  readonly provider: string;
  readonly driver: string;
  readonly model: string;
  readonly role: string | null;
  readonly recordedAt: string;
  readonly startedAt: string | null;
  readonly completedAt: string | null;
  readonly nativeSessionId: string | null;
  readonly usageJson: string | null;
}

const decodeUsage = Schema.decodeUnknownOption(Schema.fromJsonString(TurnTokenUsage));
const isUsageProvider = Schema.is(UsageProviderKind);

/** A turn has one stable ledger key even when a run is retried or the history is scanned again. */
export function accountTurn(
  row: TurnAccountingRow,
  records: readonly UsageRecord[],
  rates: RateTable,
  overrides: RateTable,
  pricingVersion: string,
): UsageTurn {
  const usage =
    row.usageJson === null ? undefined : Option.getOrUndefined(decodeUsage(row.usageJson));
  const inputTokens = usage?.inputTokens ?? 0;
  const cachedInputTokens = usage?.cachedInputTokens ?? 0;
  const cacheCreationTokens = usage?.cacheCreationTokens ?? 0;
  const outputTokens = usage?.outputTokens ?? 0;
  const totals = {
    uncachedInputTokens: Math.max(0, inputTokens - cachedInputTokens - cacheCreationTokens),
    cachedInputTokens,
    cacheCreationTokens,
    outputTokens,
    reasoningTokens: Math.min(outputTokens, usage?.reasoningTokens ?? 0),
  };
  const nativeProvider = row.driver === "claudeAgent" ? "claude" : row.driver;
  const started = row.startedAt === null ? NaN : Date.parse(row.startedAt);
  const completed = row.completedAt === null ? NaN : Date.parse(row.completedAt);
  // Some native timestamps only have second precision. Include the start's second;
  // a record is assigned only if its complete totals match the provider's turn report.
  const native = records.filter(
    (record) =>
      record.sessionId === row.nativeSessionId &&
      record.provider === nativeProvider &&
      record.timestampMs >= Math.floor(started / 1000) * 1000 &&
      record.timestampMs <= Math.ceil(completed / 1000) * 1000,
  );
  const nativeInput = native.reduce(
    (sum, record) =>
      sum +
      record.totals.uncachedInputTokens +
      record.totals.cachedInputTokens +
      record.totals.cacheCreationTokens,
    0,
  );
  const nativeOutput = native.reduce((sum, record) => sum + record.totals.outputTokens, 0);
  const matchingNative =
    native.length > 0 && nativeInput === inputTokens && nativeOutput === outputTokens;
  const complete = usage?.usageStatus === "complete";
  // An interrupted turn's report can stop short of what was billed, so its native
  // requests are both its tokens and its price. Without them it stays unpriced.
  const nativeOnly = !complete && native.length > 0;
  // Native records are single requests; the report's totals sum every request of
  // the turn, so they never select a per-prompt long-context tier.
  const priced =
    matchingNative || nativeOnly
      ? native.map((record) => priceUsage(rates, record, overrides, "request"))
      : complete
        ? [
            priceUsage(
              rates,
              { model: row.model, totals, speed: "standard", reportedCostUsd: null },
              overrides,
              "aggregate",
            ),
          ]
        : [];
  const costSource =
    priced.length === 0 || priced.some((price) => price.costSource === "unpriced")
      ? "unpriced"
      : priced.every((price) => price.costSource === "providerReported")
        ? "providerReported"
        : "modelPriced";
  const sumNative = (field: keyof UsageRecord["totals"]) =>
    native.reduce((sum, record) => sum + record.totals[field], 0);
  const counted = nativeOnly
    ? {
        inputTokens: nativeInput,
        cachedInputTokens: sumNative("cachedInputTokens"),
        cacheCreationTokens: sumNative("cacheCreationTokens"),
        outputTokens: nativeOutput,
        reasoningTokens: Math.min(nativeOutput, sumNative("reasoningTokens")),
      }
    : {
        inputTokens,
        cachedInputTokens,
        cacheCreationTokens,
        outputTokens,
        reasoningTokens: totals.reasoningTokens,
      };
  return {
    threadId: row.threadId,
    turnId: row.turnId,
    role:
      row.role === "advisor" || row.role === "lead" || row.role === "sidekick"
        ? row.role
        : "worker",
    provider: row.provider,
    ...(isUsageProvider(nativeProvider) ? { driver: nativeProvider } : {}),
    model: row.model,
    recordedAt: row.recordedAt,
    ...counted,
    usageStatus: usage?.usageStatus ?? "unavailable",
    accountingScope: "mainAgent",
    costUsd:
      costSource === "unpriced" ? null : priced.reduce((sum, price) => sum + price.costUsd, 0),
    costSource,
    pricingVersion,
  };
}

interface ReadInput {
  readonly window: UsageSummaryInput;
  readonly records: readonly UsageRecord[];
  readonly claudeSessionRecords?: readonly UsageRecord[];
  readonly rates: RateTable;
  readonly overrides: RateTable;
  readonly pricingVersion: string;
}

export interface ThreadUsageReport {
  /** Live threads with turns in the window; a sidekick's turns count toward its lead. */
  readonly threads: readonly UsageThread[];
  /** In-window turns of deleted threads, kept in totals without their titles. */
  readonly deletedThreadTurns: readonly UsageTurn[];
}

export class ThreadUsageService extends Context.Service<
  ThreadUsageService,
  {
    readonly read: (input: ReadInput) => Effect.Effect<ThreadUsageReport, UsageReadError>;
    readonly claudeSessionIds: Effect.Effect<ReadonlySet<string>, UsageReadError>;
  }
>()("t3/usage/ThreadUsageService") {}

const NativeRecord = Schema.Struct({
  provider: UsageProviderKind,
  timestampMs: Schema.Number,
  model: Schema.String,
  rateModel: Schema.optionalKey(Schema.String),
  sessionId: Schema.String,
  totals: UsageTokenTotals,
  cacheCreation1hTokens: Schema.optionalKey(Schema.Number),
  reportedCostUsd: Schema.NullOr(Schema.Number),
  speed: Schema.Literals(["standard", "fast", "ultrafast"]),
  dedupeKey: Schema.NullOr(Schema.String),
});
// Keep source fingerprints and request-level pricing inputs in SQLite, outside the wire report.
// This lets cleaned-up transcripts retain their accounting and lets price overrides reprice it.
const LedgerTurn = Schema.Struct({
  ...UsageTurn.fields,
  sourceUsageJson: Schema.optionalKey(Schema.NullOr(Schema.String)),
  inputFingerprint: Schema.optionalKey(Schema.String),
  nativeRecords: Schema.optionalKey(Schema.Array(NativeRecord)),
  claudeSessionRecords: Schema.optionalKey(Schema.Array(NativeRecord)),
});
const encodeTurn = Schema.encodeSync(Schema.fromJsonString(LedgerTurn));
const decodeLedgerTurn = Schema.decodeUnknownEffect(Schema.fromJsonString(LedgerTurn));

function publicTurn(ledger: typeof LedgerTurn.Type): UsageTurn {
  const {
    sourceUsageJson: _source,
    inputFingerprint: _fingerprint,
    nativeRecords: _native,
    claudeSessionRecords: _billing,
    ...turn
  } = ledger;
  return turn;
}

function fingerprint(value: unknown): string {
  return NodeCrypto.createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function recordsInTurn(row: TurnAccountingRow, records: readonly UsageRecord[], precise = false) {
  const started = Date.parse(row.startedAt ?? "");
  const completed = Date.parse(row.completedAt ?? "");
  return records.filter(
    (record) =>
      record.timestampMs >= (precise ? started : Math.floor(started / 1000) * 1000) &&
      record.timestampMs <= (precise ? completed : Math.ceil(completed / 1000) * 1000),
  );
}

/** Partial scans add evidence; they cannot erase requests already assigned to this turn. */
function mergeNativeRecords(previous: readonly UsageRecord[], current: readonly UsageRecord[]) {
  const merged = new Map<string, UsageRecord>();
  for (const batch of [previous, current]) {
    const occurrences = new Map<string, number>();
    for (const record of batch) {
      let key = JSON.stringify([record.provider, record.sessionId, record.dedupeKey]);
      if (record.dedupeKey === null || record.provider === "codex") {
        // Codex's copy-dedupe keys appear only when a scan finds multiple files
        // for a session. They are not stable request ids across report windows.
        // Providers without request ids can emit equal records in one timestamp.
        // Match occurrences across scans, while preserving multiplicity within each.
        const identity = fingerprint([
          record.provider,
          record.sessionId,
          record.timestampMs,
          record.model,
          record.totals,
          record.speed,
          record.cacheCreation1hTokens,
        ]);
        const occurrence = (occurrences.get(identity) ?? 0) + 1;
        occurrences.set(identity, occurrence);
        key = `${identity}:${occurrence}`;
      }
      merged.set(key, record);
    }
  }
  return [...merged]
    .sort(([a, left], [b, right]) => left.timestampMs - right.timestampMs || a.localeCompare(b))
    .map(([, record]) => record);
}

/**
 * Claude writes a cumulative cost-state when its CLI process exits, and one process
 * serves every turn until then. A snapshot bills only its own turn when no other turn
 * of the session started after the previous snapshot; otherwise the turn keeps its
 * main-agent usage, so the earlier turns are not counted twice. Without the transcript
 * there is nothing to compare, and persisted billing was checked when it was found.
 */
function ownClaudeBilling<R extends { readonly timestampMs: number }>(
  row: TurnAccountingRow,
  billing: readonly R[],
  sessionRecords: readonly UsageRecord[],
  sessionTurns: readonly { readonly turnId: string; readonly startedAtMs: number }[],
): readonly R[] {
  if (billing.length === 0 || sessionRecords.length === 0) return billing;
  const first = Math.min(...billing.map((record) => record.timestampMs));
  const previous = sessionRecords.reduce(
    (latest, record) =>
      record.timestampMs < first ? Math.max(latest, record.timestampMs) : latest,
    -Infinity,
  );
  const started = Date.parse(row.startedAt ?? "");
  const shared = sessionTurns.some(
    (turn) =>
      turn.turnId !== row.turnId && turn.startedAtMs > previous && turn.startedAtMs < started,
  );
  return shared ? [] : billing;
}

function withClaudeAccounting(
  turn: UsageTurn,
  billing: readonly UsageRecord[],
  input: ReadInput,
): UsageTurn {
  const inputTokens = billing.reduce(
    (sum, record) =>
      sum +
      record.totals.uncachedInputTokens +
      record.totals.cachedInputTokens +
      record.totals.cacheCreationTokens,
    0,
  );
  const outputTokens = billing.reduce((sum, record) => sum + record.totals.outputTokens, 0);
  // A missing final snapshot must not replace a complete turn with a partial count.
  if (billing.length === 0 || inputTokens < turn.inputTokens || outputTokens < turn.outputTokens)
    return turn;
  // Cost-state deltas sum every request of a CLI process, so they price at base tiers.
  const prices = billing.map((record) =>
    priceUsage(input.rates, record, input.overrides, "aggregate"),
  );
  const unpriced = prices.some((price) => price.costSource === "unpriced");
  return {
    ...turn,
    inputTokens,
    outputTokens,
    cachedInputTokens: billing.reduce((sum, record) => sum + record.totals.cachedInputTokens, 0),
    cacheCreationTokens: billing.reduce(
      (sum, record) => sum + record.totals.cacheCreationTokens,
      0,
    ),
    reasoningTokens: billing.reduce((sum, record) => sum + record.totals.reasoningTokens, 0),
    costUsd: unpriced ? null : prices.reduce((sum, price) => sum + price.costUsd, 0),
    costSource: unpriced
      ? "unpriced"
      : prices.every((price) => price.costSource === "providerReported")
        ? "providerReported"
        : "modelPriced",
    accountingScope: "sessionWithAuxiliary",
  };
}

function inWindow(
  turn: Pick<UsageTurn, "recordedAt">,
  window: UsageSummaryInput,
  formatter: Intl.DateTimeFormat,
): boolean {
  const time = Date.parse(turn.recordedAt);
  if (window.resolution === "hour")
    return time >= Date.parse(window.sinceTime!) && time < Date.parse(window.untilTime!);
  const day = formatter.format(new Date(time));
  return day >= window.sinceDay && day <= window.untilDay;
}

function windowBounds(window: UsageSummaryInput) {
  // SQL narrows to a UTC envelope; the final day check respects DST and the
  // requested time zone. julianday also handles older non-canonical timestamps.
  const since =
    window.resolution === "hour"
      ? window.sinceTime!
      : new Date(Date.parse(`${window.sinceDay}T00:00:00Z`) - 86_400_000).toISOString();
  const until =
    window.resolution === "hour"
      ? window.untilTime!
      : new Date(Date.parse(`${window.untilDay}T00:00:00Z`) + 2 * 86_400_000).toISOString();
  let formatter: Intl.DateTimeFormat;
  try {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: window.timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: "UTC",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }
  return { since, until, formatter };
}

export const make = Effect.gen(function* () {
  const sql = yield* SqlClient.SqlClient;
  const claudeSessionIds = sql<{ nativeId: string }>`
    SELECT DISTINCT json_extract(payload_json, '$.nativeThreadRef.nativeId') AS "nativeId"
    FROM orchestration_v2_projection_provider_threads
    WHERE provider = 'claudeAgent' AND json_extract(payload_json, '$.nativeThreadRef.nativeId') IS NOT NULL
  `.pipe(
    Effect.map((rows) => new Set(rows.map((row) => row.nativeId))),
    Effect.mapError(
      (cause) =>
        new UsageReadError({
          reason: "scanFailed",
          detail: "Thread usage sessions could not be read.",
          cause,
        }),
    ),
  );
  /** One undecodable ledger row is skipped, and re-accounted when its turn is still projected. */
  const decodeLedgerRow = (turnId: string, payload: string) =>
    decodeLedgerTurn(payload).pipe(
      Effect.map((turn): typeof LedgerTurn.Type | undefined => turn),
      Effect.catch((cause) =>
        Effect.logWarning("Skipping an undecodable usage ledger row.", { turnId, cause }).pipe(
          Effect.as(undefined),
        ),
      ),
    );
  const read = Effect.fn("ThreadUsageService.read")(function* (input: ReadInput) {
    const { since, until, formatter } = windowBounds(input.window);
    const pricingFingerprint = fingerprint([
      input.pricingVersion,
      [...input.rates].sort(([a], [b]) => a.localeCompare(b)),
      [...input.overrides].sort(([a], [b]) => a.localeCompare(b)),
    ]);
    const recordsBySession = new Map<string, UsageRecord[]>();
    for (const record of input.records) {
      const key = `${record.provider}:${record.sessionId}`;
      const records = recordsBySession.get(key) ?? [];
      records.push(record);
      recordsBySession.set(key, records);
    }
    const claudeRecordsBySession = new Map<string, UsageRecord[]>();
    for (const record of input.claudeSessionRecords ?? []) {
      const records = claudeRecordsBySession.get(record.sessionId) ?? [];
      records.push(record);
      claudeRecordsBySession.set(record.sessionId, records);
    }
    // Read sources and ledger in the same transaction, so a concurrent refresh
    // cannot overwrite newer usage with an older projection snapshot.
    const turns = yield* sql.withTransaction(
      Effect.gen(function* () {
        const previousRows = yield* sql<{ turn_id: string; payload_json: string }>`
          SELECT turn_id, payload_json FROM usage_turns
          WHERE julianday(recorded_at) >= julianday(${since})
            AND julianday(recorded_at) < julianday(${until})
        `;
        const previous = new Map<string, typeof LedgerTurn.Type>();
        for (const row of previousRows) {
          const turn = yield* decodeLedgerRow(row.turn_id, row.payload_json);
          if (turn !== undefined) previous.set(turn.turnId, turn);
        }
        const rows = yield* sql<TurnAccountingRow & { previousPayload: string | null }>`
          WITH candidates AS (
            SELECT provider_turn_id FROM orchestration_v2_projection_provider_turns
            WHERE julianday(COALESCE(completed_at, started_at)) >= julianday(${since})
              AND julianday(COALESCE(completed_at, started_at)) < julianday(${until})
            UNION
            SELECT turn.provider_turn_id FROM orchestration_v2_projection_provider_turns turn
            JOIN orchestration_v2_projection_threads thread ON thread.thread_id = turn.thread_id
            WHERE turn.completed_at IS NULL AND turn.started_at IS NULL
              AND julianday(thread.updated_at) >= julianday(${since})
              AND julianday(thread.updated_at) < julianday(${until})
            UNION
            SELECT turn_id FROM usage_turns
            WHERE julianday(recorded_at) >= julianday(${since})
              AND julianday(recorded_at) < julianday(${until})
          )
          SELECT turn.provider_turn_id AS "turnId", turn.thread_id AS "threadId",
            pt.provider AS driver,
            json_extract(pt.payload_json, '$.providerInstanceId') AS provider,
            COALESCE(json_extract(run.payload_json, '$.modelSelection.model'),
              json_extract(pt.payload_json, '$.nativeMetadata.modelSelection.model'), 'unknown') AS model,
            json_extract(run.payload_json, '$.usageRole') AS role,
            COALESCE(turn.completed_at, turn.started_at, thread.updated_at) AS "recordedAt",
            turn.started_at AS "startedAt", turn.completed_at AS "completedAt",
            json_extract(pt.payload_json, '$.nativeThreadRef.nativeId') AS "nativeSessionId",
            json_extract(turn.payload_json, '$.turnTokenUsage') AS "usageJson",
            ledger.payload_json AS "previousPayload"
          FROM candidates
          JOIN orchestration_v2_projection_provider_turns turn ON turn.provider_turn_id = candidates.provider_turn_id
          JOIN orchestration_v2_projection_threads thread ON thread.thread_id = turn.thread_id
          JOIN orchestration_v2_projection_provider_threads pt ON pt.provider_thread_id = turn.provider_thread_id
          LEFT JOIN orchestration_v2_projection_run_attempts attempt ON attempt.attempt_id = turn.run_attempt_id
          LEFT JOIN orchestration_v2_projection_runs run ON run.run_id = attempt.run_id
          LEFT JOIN usage_turns ledger ON ledger.turn_id = turn.provider_turn_id
        `;
        // Every turn of these sessions, not just the window's, so a snapshot's
        // turns are found even when they fall in an earlier hour or day.
        const claudeSessions = [
          ...new Set(
            rows.flatMap((row) =>
              row.driver === "claudeAgent" &&
              row.nativeSessionId !== null &&
              claudeRecordsBySession.has(row.nativeSessionId)
                ? [row.nativeSessionId]
                : [],
            ),
          ),
        ];
        const sessionTurnRows =
          claudeSessions.length === 0
            ? []
            : yield* sql<{ nativeSessionId: string; turnId: string; startedAt: string }>`
                SELECT json_extract(pt.payload_json, '$.nativeThreadRef.nativeId') AS "nativeSessionId",
                  turn.provider_turn_id AS "turnId", turn.started_at AS "startedAt"
                FROM orchestration_v2_projection_provider_threads pt
                JOIN orchestration_v2_projection_provider_turns turn ON turn.provider_thread_id = pt.provider_thread_id
                WHERE pt.provider = 'claudeAgent' AND turn.started_at IS NOT NULL
                  AND json_extract(pt.payload_json, '$.nativeThreadRef.nativeId') IN ${sql.in(claudeSessions)}
              `;
        const turnsBySession = new Map<string, { turnId: string; startedAtMs: number }[]>();
        for (const turn of sessionTurnRows) {
          const turns = turnsBySession.get(turn.nativeSessionId) ?? [];
          turns.push({ turnId: turn.turnId, startedAtMs: Date.parse(turn.startedAt) });
          turnsBySession.set(turn.nativeSessionId, turns);
        }
        for (const { previousPayload, ...row } of rows) {
          const provider = row.driver === "claudeAgent" ? "claude" : row.driver;
          const prior =
            previous.get(row.turnId) ??
            (previousPayload === null
              ? undefined
              : yield* decodeLedgerRow(row.turnId, previousPayload));
          if (
            !inWindow({ recordedAt: row.recordedAt }, input.window, formatter) &&
            prior === undefined
          )
            continue;
          const unchanged = prior?.sourceUsageJson === row.usageJson;
          const foundNative = recordsInTurn(
            row,
            recordsBySession.get(`${provider}:${row.nativeSessionId}`) ?? [],
          );
          const retainedNative = recordsInTurn(row, prior?.nativeRecords ?? []).filter(
            (record) => record.provider === provider && record.sessionId === row.nativeSessionId,
          );
          const nativeRecords = mergeNativeRecords(retainedNative, foundNative);
          // These cumulative native snapshots include the CLI's billable auxiliary calls.
          // Native subagents retain the main-agent accounting path to avoid charging children twice.
          const usage =
            row.usageJson === null ? undefined : Option.getOrUndefined(decodeUsage(row.usageJson));
          const sessionRecords = claudeRecordsBySession.get(row.nativeSessionId ?? "") ?? [];
          const sessionTurns = turnsBySession.get(row.nativeSessionId ?? "") ?? [];
          const foundBilling =
            row.driver === "claudeAgent" && usage?.usageStatus === "complete" && !usage.hasSubagents
              ? ownClaudeBilling(
                  row,
                  recordsInTurn(row, sessionRecords, true),
                  sessionRecords,
                  sessionTurns,
                )
              : [];
          // Also re-checks ledger rows written before snapshots were matched to their turns.
          const retainedBilling = unchanged
            ? ownClaudeBilling(row, prior?.claudeSessionRecords ?? [], sessionRecords, sessionTurns)
            : [];
          const billedTokens = (records: readonly UsageRecord[]) =>
            records.reduce(
              (sum, record) =>
                sum +
                record.totals.uncachedInputTokens +
                record.totals.cachedInputTokens +
                record.totals.cacheCreationTokens +
                record.totals.outputTokens,
              0,
            );
          const billing =
            billedTokens(foundBilling) >= billedTokens(retainedBilling)
              ? foundBilling
              : retainedBilling;
          const inputFingerprint = fingerprint([row, nativeRecords, billing, pricingFingerprint]);
          if (prior?.inputFingerprint === inputFingerprint) continue;
          const turn = withClaudeAccounting(
            accountTurn(row, nativeRecords, input.rates, input.overrides, input.pricingVersion),
            billing,
            input,
          );
          const persisted = {
            ...turn,
            sourceUsageJson: row.usageJson,
            inputFingerprint,
            nativeRecords,
            claudeSessionRecords: billing,
          };
          yield* sql`
        INSERT INTO usage_turns (turn_id, thread_id, recorded_at, payload_json)
        VALUES (${turn.turnId}, ${turn.threadId}, ${turn.recordedAt}, ${encodeTurn(persisted)})
        ON CONFLICT(turn_id) DO UPDATE SET recorded_at = excluded.recorded_at, payload_json = excluded.payload_json
        WHERE usage_turns.payload_json <> excluded.payload_json
      `;
          previous.set(turn.turnId, persisted);
        }
        return [...previous.values()].map(publicTurn);
      }),
    );

    // Only threads with ledger turns in the window, and the leads their sidekicks work for.
    const threadRows = yield* sql<{
      threadId: string;
      projectId: string;
      title: string;
      parentThreadId: string | null;
      deleted: number;
    }>`
      WITH used AS (
        SELECT DISTINCT thread_id FROM usage_turns
        WHERE julianday(recorded_at) >= julianday(${since})
          AND julianday(recorded_at) < julianday(${until})
      )
      SELECT thread.thread_id AS "threadId", thread.project_id AS "projectId",
        json_extract(thread.payload_json, '$.title') AS title,
        json_extract(thread.payload_json, '$.lineage.parentThreadId') AS "parentThreadId",
        thread.deleted_at IS NOT NULL AS deleted
      FROM orchestration_v2_projection_threads thread
      WHERE thread.thread_id IN (SELECT thread_id FROM used)
        OR thread.thread_id IN (
          SELECT json_extract(child.payload_json, '$.lineage.parentThreadId')
          FROM orchestration_v2_projection_threads child
          JOIN used ON used.thread_id = child.thread_id
        )
      ORDER BY thread.updated_at DESC
    `;
    const threadById = new Map(threadRows.map((thread) => [thread.threadId, thread]));
    const byThread = new Map<string, UsageTurn[]>();
    const deletedThreadTurns: UsageTurn[] = [];
    turns.sort(
      (a, b) => a.recordedAt.localeCompare(b.recordedAt) || a.turnId.localeCompare(b.turnId),
    );
    for (const turn of turns) {
      if (!inWindow(turn, input.window, formatter)) continue;
      // A sidekick works for its lead, so its turns count toward the lead's thread.
      const ownerId =
        turn.role === "sidekick"
          ? (threadById.get(turn.threadId)?.parentThreadId ?? turn.threadId)
          : turn.threadId;
      const owner = threadById.get(ownerId);
      // Deleted threads keep their spend in the totals, but not their titles.
      if (owner === undefined || owner.deleted !== 0) {
        deletedThreadTurns.push(turn);
        continue;
      }
      const list = byThread.get(ownerId) ?? [];
      list.push(turn);
      byThread.set(ownerId, list);
    }
    const threads = threadRows.flatMap(
      ({ parentThreadId: _parentThreadId, deleted: _deleted, ...thread }): UsageThread[] => {
        const threadTurns = byThread.get(thread.threadId);
        return threadTurns === undefined ? [] : [{ ...thread, turns: threadTurns }];
      },
    );
    return { threads, deletedThreadTurns } satisfies ThreadUsageReport;
  });
  return {
    claudeSessionIds,
    read: (input: ReadInput) =>
      read(input).pipe(
        Effect.catchCause((cause) =>
          Effect.fail(
            new UsageReadError({
              reason: "scanFailed",
              detail: "Thread usage accounting could not be read or persisted.",
              cause,
            }),
          ),
        ),
      ),
  };
});

export const layer = Layer.effect(ThreadUsageService, make);
export const layerTest = Layer.succeed(ThreadUsageService, {
  read: () => Effect.succeed({ threads: [], deletedThreadTurns: [] }),
  claudeSessionIds: Effect.succeed(new Set<string>()),
});
