import type { UsageRole, UsageTurn } from "@t3tools/contracts";

const USAGE_ROLE_LABEL: Record<UsageRole, string> = {
  worker: "Worker",
  lead: "Lead",
  sidekick: "Sidekick",
  advisor: "Advisor",
};

/** The label clients show on a run's messages and plans. */
export function usageRoleLabel(role: UsageRole): string {
  return USAGE_ROLE_LABEL[role];
}

export function summarizeRole(turns: readonly UsageTurn[], role: UsageRole) {
  let costUsd = 0;
  let inputTokens = 0;
  let cachedInputTokens = 0;
  let outputTokens = 0;
  let unpricedTurns = 0;
  let count = 0;
  for (const turn of turns) {
    if (turn.role !== role) continue;
    count += 1;
    costUsd += turn.costUsd ?? 0;
    inputTokens += turn.inputTokens;
    cachedInputTokens += turn.cachedInputTokens;
    outputTokens += turn.outputTokens;
    if (turn.costUsd === null) unpricedTurns += 1;
  }
  return { costUsd, inputTokens, cachedInputTokens, outputTokens, unpricedTurns, count };
}

export interface ExportUsageTurn {
  readonly environment: string;
  readonly threadTitle: string;
  readonly turn: UsageTurn;
}

/** RFC 4180, with formula-like text escaped for spreadsheet imports. */
function csvCell(value: string | number | null): string {
  const text = value === null ? "" : String(value);
  const safe = /^[\s]*[=+@-]/.test(text) ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
}

/** Fixed-point USD, so spreadsheets never read a tiny cost such as 1.5e-7 as text or zero. */
function fixedUsd(value: number): string {
  return value.toFixed(12).replace(/\.?0+$/, "");
}

export function usageTurnsCsv(rows: readonly ExportUsageTurn[]): string {
  const header = [
    "environment",
    "threadId",
    "threadTitle",
    "turnId",
    "role",
    "provider",
    "model",
    "recordedAt",
    "inputTokens",
    "cachedInputTokens",
    "cacheCreationTokens",
    "outputTokens",
    "reasoningTokens",
    "usageStatus",
    "accountingScope",
    "costUsd",
    "costSource",
    "pricingVersion",
  ];
  return (
    [
      header.map(csvCell).join(","),
      ...rows.map(({ environment, threadTitle, turn }) =>
        [
          environment,
          turn.threadId,
          threadTitle,
          turn.turnId,
          turn.role,
          turn.provider,
          turn.model,
          turn.recordedAt,
          turn.inputTokens,
          turn.cachedInputTokens,
          turn.cacheCreationTokens,
          turn.outputTokens,
          turn.reasoningTokens,
          turn.usageStatus,
          turn.accountingScope ?? "mainAgent",
          turn.costUsd === null ? null : fixedUsd(turn.costUsd),
          turn.costSource,
          turn.pricingVersion,
        ]
          .map(csvCell)
          .join(","),
      ),
    ].join("\r\n") + "\r\n"
  );
}
