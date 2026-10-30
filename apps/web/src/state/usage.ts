/**
 * Multi-environment usage state.
 *
 * Every connected environment answers the same typed query; the client merges
 * the results. Raw transcripts never leave the machine that produced them.
 *
 * @module state/usage
 */
import { useAtomValue } from "@effect/atom-react";
import {
  AuthDiagnosticsReadScope,
  USAGE_CONTRACT_VERSION,
  type EnvironmentId,
  type ScopedThreadRef,
  type UsageBucket,
  type UsageSummary,
  type UsageProviderKind,
  type UsageSummaryInput,
  type UsageTurn,
  UsageDay,
} from "@t3tools/contracts";
import { needsCursorKeychainAccess, refreshUsage } from "@t3tools/client-runtime/state/usage";
import { resolveUsageAccess } from "@t3tools/client-runtime/state/usage-access";
import * as Option from "effect/Option";
import { AsyncResult, Atom } from "effect/reactivity";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { makeWindow } from "@t3tools/shared/usageFormat";
import { mergeUsage, type EnvironmentUsage, type MergedUsage } from "@t3tools/shared/usageMerge";
import { appAtomRegistry } from "../rpc/atomRegistry";
import { environmentPresentations } from "./presentation";
import { serverEnvironment } from "./server";
import { environmentSession, readEnvironmentScope, useEnvironmentScope } from "./session";

export interface EnvironmentUsageStatus {
  readonly environmentId: EnvironmentId;
  readonly label: string;
  readonly isPending: boolean;
  readonly canReadDiagnostics: boolean;
  readonly isConnected: boolean;
  readonly error: string | null;
  readonly summary: UsageSummary | null;
  readonly needsCursorKeychainAccess: boolean;
}

/**
 * Reads every environment's summary for one window.
 *
 * Keyed by the serialised window so switching ranges does not thrash the atom
 * cache, and so each environment's query is shared with any other reader of the
 * same window.
 */
const usageByWindowAtom = Atom.family((windowKey: string) =>
  Atom.make((get): readonly EnvironmentUsageStatus[] => {
    const input = JSON.parse(windowKey) as UsageSummaryInput;
    const presentations = get(environmentPresentations.presentationsAtom);

    const statuses: EnvironmentUsageStatus[] = [];
    for (const [environmentId, presentation] of presentations) {
      const isConnected = presentation.connection.phase === "connected";
      const sessionResult = get(environmentSession.sessionStateAtom(environmentId));
      const access = resolveUsageAccess({
        connectionPhase: presentation.connection.phase,
        session: Option.getOrNull(AsyncResult.value(sessionResult)),
        hasSessionError: sessionResult._tag === "Failure",
      });
      if (!access.canReadDiagnostics) {
        statuses.push({
          environmentId,
          label: presentation.entry.target.label,
          isConnected,
          ...access,
          summary: null,
          needsCursorKeychainAccess: false,
        });
        continue;
      }
      const result = get(serverEnvironment.usageSummary({ environmentId, input }));
      const summary = Option.getOrNull(AsyncResult.value(result));
      statuses.push({
        environmentId,
        label: presentation.entry.target.label,
        isPending: result.waiting,
        canReadDiagnostics: true,
        isConnected,
        error: result._tag === "Failure" ? "This environment could not report usage." : null,
        summary,
        needsCursorKeychainAccess: needsCursorKeychainAccess(
          summary,
          get(serverEnvironment.providersValueAtom(environmentId)),
        ),
      });
    }
    return statuses;
  }).pipe(Atom.withLabel(`web-usage:window:${windowKey}`)),
);

export interface UsageView {
  readonly merged: MergedUsage;
  readonly environments: readonly EnvironmentUsageStatus[];
  readonly selectedEnvironments: readonly EnvironmentUsageStatus[];
  /** True until at least one selected environment has answered. */
  readonly isPending: boolean;
  /**
   * The usage to draw: this window's once a selected environment answers,
   * until then the last window answered for the same selection. Null while
   * nothing has answered and something still could.
   */
  readonly shown: { readonly window: UsageSummaryInput; readonly merged: MergedUsage } | null;
  /**
   * True while environments that have not failed are still answering. Failed
   * environments are reported through their own error rows: totals will not
   * improve by waiting on them, so they must not read as "still reporting".
   */
  readonly isPartial: boolean;
  readonly refresh: (input?: UsageSummaryInput) => Promise<void>;
}

/**
 * Merges every environment that has answered. `keepBucket` narrows the merge,
 * for example to one model; source ownership still applies, so the result
 * matches that slice of the full merge. Session counts are per directory and
 * are not narrowed.
 */
export function mergeAnsweredUsage(
  environments: readonly EnvironmentUsageStatus[],
  keepBucket?: (bucket: UsageBucket) => boolean,
): MergedUsage {
  const answered: EnvironmentUsage[] = environments.flatMap(({ environmentId, label, summary }) =>
    summary === null
      ? []
      : [
          {
            environmentId,
            label,
            summary:
              keepBucket === undefined
                ? summary
                : { ...summary, buckets: summary.buckets.filter(keepBucket) },
          },
        ],
  );
  return mergeUsage(answered, USAGE_CONTRACT_VERSION);
}

const NO_HIDDEN_PROVIDERS: ReadonlySet<UsageProviderKind> = new Set();

/**
 * Drops hidden providers' buckets and sources before merging, so totals,
 * shares, and session counts all describe only the visible providers.
 */
function withoutProviders(
  environments: readonly EnvironmentUsageStatus[],
  hiddenProviders: ReadonlySet<UsageProviderKind>,
): readonly EnvironmentUsageStatus[] {
  if (hiddenProviders.size === 0) return environments;
  return environments.map((environment) =>
    environment.summary === null
      ? environment
      : {
          ...environment,
          summary: {
            ...environment.summary,
            buckets: environment.summary.buckets.filter(
              (bucket) => !hiddenProviders.has(bucket.provider),
            ),
            sources: environment.summary.sources.filter(
              (source) => !hiddenProviders.has(source.fingerprint.provider),
            ),
          },
        },
  );
}

export function useUsage(
  input: UsageSummaryInput,
  selectedEnvironmentIds: ReadonlySet<EnvironmentId> | null = null,
  hiddenProviders: ReadonlySet<UsageProviderKind> = NO_HIDDEN_PROVIDERS,
): UsageView {
  const windowKey = useMemo(
    () =>
      JSON.stringify({
        sinceDay: input.sinceDay,
        untilDay: input.untilDay,
        timeZone: input.timeZone,
        resolution: input.resolution,
        sinceTime: input.sinceTime,
        untilTime: input.untilTime,
      }),
    [
      input.sinceDay,
      input.untilDay,
      input.timeZone,
      input.resolution,
      input.sinceTime,
      input.untilTime,
    ],
  );
  const atom = usageByWindowAtom(windowKey);
  const environments = useAtomValue(atom);
  const selectedEnvironments = useMemo(
    () =>
      selectedEnvironmentIds === null
        ? environments
        : environments.filter((environment) =>
            selectedEnvironmentIds.has(environment.environmentId),
          ),
    [environments, selectedEnvironmentIds],
  );

  const refresh = useCallback(
    (nextInput?: UsageSummaryInput) =>
      refreshUsage({
        registry: appAtomRegistry,
        server: serverEnvironment,
        presentations: environmentPresentations,
        // Only environments this connection may read; the others report a
        // permission error instead of a stale or failed rescan.
        environmentIds: selectedEnvironments
          .filter(
            (environment) =>
              environment.canReadDiagnostics &&
              readEnvironmentScope(environment.environmentId, AuthDiagnosticsReadScope),
          )
          .map(({ environmentId }) => environmentId),
        input: nextInput ?? (JSON.parse(windowKey) as UsageSummaryInput),
      }),
    [selectedEnvironments, windowKey],
  );

  const merged = useMemo(
    () => mergeAnsweredUsage(withoutProviders(selectedEnvironments, hiddenProviders)),
    [selectedEnvironments, hiddenProviders],
  );

  const answeredCount = selectedEnvironments.filter(
    (environment) => environment.summary !== null,
  ).length;
  const stillReporting = selectedEnvironments.filter(
    (environment) => environment.summary === null && environment.error === null,
  ).length;
  const isPending = answeredCount === 0 && stillReporting > 0;

  // Stored during render, as React recommends for state that follows props, so
  // the kept usage is on screen in the same frame the new window starts pending.
  const [lastAnswered, setLastAnswered] = useState<
    | (NonNullable<UsageView["shown"]> & {
        readonly selection: typeof selectedEnvironmentIds;
        readonly hidden: typeof hiddenProviders;
      })
    | null
  >(null);
  if (
    answeredCount > 0 &&
    (lastAnswered?.merged !== merged ||
      lastAnswered.window !== input ||
      lastAnswered.selection !== selectedEnvironmentIds ||
      lastAnswered.hidden !== hiddenProviders)
  ) {
    setLastAnswered({
      window: input,
      merged,
      selection: selectedEnvironmentIds,
      hidden: hiddenProviders,
    });
  }
  // Kept usage only stands in for the same environments and provider filter.
  const kept =
    lastAnswered?.selection === selectedEnvironmentIds && lastAnswered.hidden === hiddenProviders
      ? lastAnswered
      : null;
  // With no answers, even failed ones keep the last answered usage on screen.
  const shown =
    answeredCount > 0
      ? { window: input, merged }
      : (kept ?? (isPending ? null : { window: input, merged }));

  return {
    merged,
    environments,
    selectedEnvironments,
    isPending,
    shown,
    isPartial: answeredCount > 0 && stillReporting > 0,
    refresh,
  };
}

const NO_USAGE_TURNS: readonly UsageTurn[] = [];
const NO_USAGE_SUMMARY_ATOM = Atom.make(AsyncResult.initial<UsageSummary>()).pipe(
  Atom.withLabel("web-usage:thread:none"),
);

/** Today's window, stretched back to the local day the thread's first message was sent. */
function threadUsageWindow(sinceIso: string | null): UsageSummaryInput {
  const today = makeWindow(1);
  if (sinceIso === null || Number.isNaN(Date.parse(sinceIso))) return today;
  const sinceDay = new Intl.DateTimeFormat("en-CA", {
    timeZone: today.timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(sinceIso));
  return sinceDay < today.sinceDay ? { ...today, sinceDay: UsageDay.make(sinceDay) } : today;
}

/**
 * One thread's priced turns, from its environment's usage summary over the days
 * since `sinceIso`. Rescans whenever `settledKey` changes, so a round's cost shows
 * up once that round settles. Empty without diagnostics access.
 */
export function useThreadUsageTurns(
  threadRef: ScopedThreadRef | null,
  sinceIso: string | null,
  settledKey: string,
): readonly UsageTurn[] {
  const environmentId = threadRef?.environmentId ?? null;
  // Reactive, so a page that renders before its session loads still starts reading.
  const canRead = useEnvironmentScope(environmentId, AuthDiagnosticsReadScope);
  const windowKey = JSON.stringify(threadUsageWindow(sinceIso));
  const query = useMemo(
    () =>
      canRead && environmentId !== null
        ? serverEnvironment.usageSummary({
            environmentId,
            input: JSON.parse(windowKey) as UsageSummaryInput,
          })
        : NO_USAGE_SUMMARY_ATOM,
    [canRead, environmentId, windowKey],
  );
  const result = useAtomValue(query);

  const lastSettledKey = useRef(settledKey);
  useEffect(() => {
    if (lastSettledKey.current === settledKey) return;
    lastSettledKey.current = settledKey;
    if (query !== NO_USAGE_SUMMARY_ATOM) appAtomRegistry.refresh(query);
  }, [query, settledKey]);

  const summary = Option.getOrNull(AsyncResult.value(result));
  return useMemo(
    () =>
      summary?.threadUsage?.find((thread) => thread.threadId === threadRef?.threadId)?.turns ??
      NO_USAGE_TURNS,
    [summary, threadRef?.threadId],
  );
}
