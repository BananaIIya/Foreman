import type { EnvironmentId } from "@t3tools/contracts";
import { makeWindow } from "@t3tools/shared/usageFormat";
import { useState } from "react";
import { View } from "react-native";

import { AppText as Text } from "../../components/AppText";
import { useEnvironmentUsageSummary } from "../../state/usage";
import { threadRoleCosts } from "./threadUsagePresentation";

const WINDOW_DAYS = 90;

/**
 * Read-only cost by role for one thread and its sidekick child. Mounted only
 * when asked for, since a cold usage scan can take seconds.
 */
export function ThreadUsageSummary(props: {
  environmentId: EnvironmentId;
  threadIds: ReadonlyArray<string>;
}) {
  // Fixed per mount so the query key stays stable while the view is open.
  const [usageWindow] = useState(() => makeWindow(WINDOW_DAYS));
  const usage = useEnvironmentUsageSummary(props.environmentId, usageWindow);
  if (usage.kind === "pending")
    return <Text className="text-xs text-foreground-muted">Loading usage…</Text>;
  const costs =
    usage.kind === "ready" ? threadRoleCosts(usage.summary.threadUsage, props.threadIds) : null;
  if (costs === null)
    return (
      <Text className="text-xs text-foreground-muted">
        Thread usage is not available from this environment.
      </Text>
    );
  if (costs.length === 0)
    return (
      <Text className="text-xs text-foreground-muted">
        No turns in the past {WINDOW_DAYS} days.
      </Text>
    );
  return (
    <View accessibilityLabel={`Thread cost, past ${WINDOW_DAYS} days`} className="gap-1">
      {costs.map((row) => (
        <View key={row.role} className="flex-row items-center justify-between">
          <Text className="text-xs text-foreground">{row.label}</Text>
          <Text className="text-xs text-foreground-muted" style={{ fontVariant: ["tabular-nums"] }}>
            {row.cost} · {row.turns} {row.turns === 1 ? "turn" : "turns"}
          </Text>
        </View>
      ))}
      <Text className="text-xs text-foreground-muted">Past {WINDOW_DAYS} days · API estimates</Text>
    </View>
  );
}
