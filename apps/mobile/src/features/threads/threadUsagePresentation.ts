import type { UsageRole, UsageThread } from "@t3tools/contracts";
import { formatUsd } from "@t3tools/shared/usageFormat";
import { summarizeRole, usageRoleLabel } from "@t3tools/shared/usageThread";

const ROLES: ReadonlyArray<UsageRole> = ["worker", "lead", "sidekick", "advisor"];

export interface ThreadRoleCost {
  readonly role: UsageRole;
  readonly label: string;
  readonly cost: string;
  readonly turns: number;
}

/** Sub-cent spend stays visible instead of rounding to $0.00. */
function formatCost(costUsd: number): string {
  return costUsd > 0 && costUsd < 0.01 ? `$${costUsd.toFixed(4)}` : formatUsd(costUsd);
}

/**
 * A thread's cost by role, including its sidekick child thread's turns. Null
 * when the summary has no thread section (older servers, or no permission to
 * read threads), so the caller can hide the view rather than claim zero.
 */
export function threadRoleCosts(
  threadUsage: ReadonlyArray<UsageThread> | undefined,
  threadIds: ReadonlyArray<string>,
): ReadonlyArray<ThreadRoleCost> | null {
  if (threadUsage === undefined) return null;
  const turns = threadUsage
    .filter((thread) => threadIds.includes(thread.threadId))
    .flatMap((thread) => thread.turns);
  return ROLES.flatMap((role) => {
    const usage = summarizeRole(turns, role);
    if (usage.count === 0) return [];
    const cost =
      usage.unpricedTurns === usage.count
        ? "Unpriced"
        : usage.unpricedTurns > 0
          ? `${formatCost(usage.costUsd)} + unpriced`
          : formatCost(usage.costUsd);
    return [{ role, label: usageRoleLabel(role), cost, turns: usage.count }];
  });
}
