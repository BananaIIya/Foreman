import { useMemo, useState } from "react";
import type { UsageProviderKind, UsageRole, UsageThread, UsageTurn } from "@t3tools/contracts";
import { summarizeRole, usageTurnsCsv } from "@t3tools/shared/usageThread";
import { formatCount } from "@t3tools/shared/usageFormat";
import type { EnvironmentUsageStatus } from "../../state/usage";
import { Button } from "../ui/button";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "../ui/select";

type ThreadEntry = UsageThread & {
  readonly key: string;
  readonly environment: string;
  readonly deleted: boolean;
};

const dollars = (value: number) => `$${value.toFixed(6)}`;
const ROLES: ReadonlyArray<{ readonly role: UsageRole; readonly label: string }> = [
  { role: "worker", label: "Worker" },
  { role: "lead", label: "Lead" },
  { role: "sidekick", label: "Sidekick" },
  { role: "advisor", label: "Advisor" },
];
const roleCost = (usage: ReturnType<typeof summarizeRole>) =>
  usage.unpricedTurns > 0
    ? usage.unpricedTurns === usage.count
      ? "Unpriced"
      : `${dollars(usage.costUsd)} + unpriced`
    : dollars(usage.costUsd);

export function UsageThreads({
  environments,
  hiddenProviders,
}: {
  readonly environments: readonly EnvironmentUsageStatus[];
  readonly hiddenProviders: ReadonlySet<UsageProviderKind>;
}) {
  const [selection, setSelection] = useState("all");
  const threads = useMemo(() => {
    const visible = (turns: readonly UsageTurn[]) =>
      turns.filter(
        (turn) =>
          !hiddenProviders.has(
            turn.driver ??
              ((turn.provider === "claudeAgent" ? "claude" : turn.provider) as UsageProviderKind),
          ),
      );
    return environments.flatMap((environment): ThreadEntry[] => {
      const deleted = environment.summary?.deletedThreadUsage ?? [];
      return [
        ...(environment.summary?.threadUsage ?? []).map((thread) => ({
          ...thread,
          key: `${environment.environmentId}:${thread.threadId}`,
          environment: environment.label,
          deleted: false,
          turns: visible(thread.turns),
        })),
        // Deleted threads keep their spend in the totals as one row, without their titles.
        ...(deleted.length === 0
          ? []
          : [
              {
                threadId: "",
                projectId: "",
                title: "Deleted threads",
                key: `${environment.environmentId}:deleted`,
                environment: environment.label,
                deleted: true,
                turns: visible(deleted),
              },
            ]),
      ];
    });
  }, [environments, hiddenProviders]);
  const selected = useMemo(
    () => threads.filter((thread) => selection === "all" || thread.key === selection),
    [threads, selection],
  );
  const turns = useMemo(() => selected.flatMap((thread) => thread.turns), [selected]);
  // Worker and Advisor stay as before; Lead and Sidekick appear once a sidekick thread has turns.
  const roles = useMemo(
    () =>
      ROLES.filter(
        ({ role }) =>
          role === "worker" ||
          role === "advisor" ||
          threads.some((thread) => thread.turns.some((turn) => turn.role === role)),
      ),
    [threads],
  );
  const totals = useMemo(
    () => roles.map(({ role, label }) => ({ role, label, usage: summarizeRole(turns, role) })),
    [roles, turns],
  );
  const rows = useMemo(
    () =>
      threads.map((thread) => ({
        thread,
        usage: roles.map(({ role }) => ({ role, usage: summarizeRole(thread.turns, role) })),
      })),
    [threads, roles],
  );
  const selectedRows = useMemo(
    () => rows.filter(({ thread }) => selection === "all" || thread.key === selection),
    [rows, selection],
  );
  const download = (report: readonly ThreadEntry[]) => {
    const csv = usageTurnsCsv(
      report.flatMap((thread) =>
        thread.turns.map((turn) => ({
          environment: thread.environment,
          threadTitle: thread.deleted ? "Deleted thread" : thread.title,
          turn,
        })),
      ),
    );
    const url = URL.createObjectURL(new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "t3-thread-usage.csv";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  if (!environments.some((environment) => environment.summary?.threadUsage !== undefined))
    return null;
  return (
    <section aria-label="Thread usage" className="space-y-4 border-t pt-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-medium">Thread usage</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={selection} onValueChange={(value) => setSelection(value ?? "all")}>
            <SelectTrigger aria-label="Usage thread" className="max-w-72" size="compact">
              <SelectValue>
                {selection === "all"
                  ? "All threads"
                  : (threads.find((thread) => thread.key === selection)?.title ?? "Select thread")}
              </SelectValue>
            </SelectTrigger>
            <SelectPopup>
              <SelectItem value="all">All threads</SelectItem>
              {threads.map((thread) => (
                <SelectItem key={thread.key} value={thread.key}>
                  {thread.deleted
                    ? `${thread.title} · ${thread.environment}`
                    : `${thread.title} · ${thread.threadId.slice(0, 8)}`}
                </SelectItem>
              ))}
            </SelectPopup>
          </Select>
          <Button
            size="sm"
            variant="outline"
            onClick={() => download(selected)}
            aria-label="Export thread usage CSV"
          >
            Export CSV
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        T3 threads in the selected period · API estimates · A Claude turn includes CLI auxiliary
        usage only when its CLI process served that turn alone; otherwise it counts the main agent.
        Input includes cached input; reasoning is included in output.
      </p>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {totals.map(({ role, label, usage }) => (
          <div key={role} className="rounded-lg border p-3">
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="text-lg font-medium tabular-nums">{roleCost(usage)}</div>
            <div className="text-xs text-muted-foreground">
              {formatCount(usage.count)} turns ·{" "}
              {formatCount(usage.inputTokens + usage.outputTokens)} tokens
              {usage.unpricedTurns > 0 ? ` · ${usage.unpricedTurns} unpriced` : ""}
            </div>
          </div>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="p-2 text-left">Thread</th>
              {roles.map(({ role, label }) => (
                <th key={role} className="p-2 text-right">
                  {label} USD
                </th>
              ))}
              <th className="p-2 text-right">Turns</th>
            </tr>
          </thead>
          <tbody>
            {selectedRows.map(({ thread, usage }) => (
              <tr key={thread.key} className="border-t">
                <td className="max-w-64 p-2">
                  <button
                    className="block truncate text-left hover:underline"
                    onClick={() => setSelection(thread.key)}
                    aria-label={
                      thread.deleted ? thread.title : `${thread.title} · ${thread.threadId}`
                    }
                  >
                    {thread.title}
                  </button>
                  <div className="text-muted-foreground">{thread.environment}</div>
                </td>
                {usage.map(({ role, usage: roleUsage }) => (
                  <td key={role} className="p-2 text-right tabular-nums">
                    {roleCost(roleUsage)}
                  </td>
                ))}
                <td className="p-2 text-right tabular-nums">{thread.turns.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selection !== "all" ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted-foreground">
              <tr>
                {["Role", "Provider / model", "Input", "Cached input", "Output", "USD"].map(
                  (label) => (
                    <th key={label} className="p-2 text-left">
                      {label}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {turns.map((turn) => (
                <tr key={turn.turnId} className="border-t">
                  <td className="p-2">{turn.role}</td>
                  <td className="p-2">
                    {turn.provider} / {turn.model}
                  </td>
                  <td className="p-2 tabular-nums">{formatCount(turn.inputTokens)}</td>
                  <td className="p-2 tabular-nums">{formatCount(turn.cachedInputTokens)}</td>
                  <td className="p-2 tabular-nums">{formatCount(turn.outputTokens)}</td>
                  <td className="p-2 tabular-nums">
                    {turn.costUsd === null ? "Unpriced" : dollars(turn.costUsd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {turns.length === 0 ? (
            <p className="py-4 text-muted-foreground">No provider turns in this period.</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
