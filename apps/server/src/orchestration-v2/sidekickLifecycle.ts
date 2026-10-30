import type { OrchestrationV2AppThread } from "@t3tools/contracts";
import { modelSelectionsEqual } from "@t3tools/shared/model";
import { exceededDispatchModeLimit } from "./DispatchModeLimit.ts";

/** A reusable session belongs to this lead, configuration and workspace, within its modes. */
export function isReusableSidekick(
  lead: OrchestrationV2AppThread,
  child: OrchestrationV2AppThread,
): boolean {
  return (
    lead.sidekick?.threadId === child.id &&
    lead.deletedAt === null &&
    lead.archivedAt === null &&
    child.deletedAt === null &&
    child.archivedAt === null &&
    child.lineage.parentThreadId === lead.id &&
    child.lineage.relationshipToParent === "subagent" &&
    child.forkedFrom?.type === "node" &&
    child.projectId === lead.projectId &&
    child.worktreePath === lead.worktreePath &&
    child.branch === lead.branch &&
    child.advisor == null &&
    child.sidekick == null &&
    modelSelectionsEqual(child.modelSelection, lead.sidekick.modelSelection) &&
    exceededDispatchModeLimit(lead, child) === undefined
  );
}
