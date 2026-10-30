import type { OrchestrationV2TurnItem } from "@t3tools/contracts";
import { toolOutputIndicatesFailure } from "@t3tools/shared/toolOutput";

/**
 * Text the lead's `sidekick_delegate` sends to its sidekick, and the report it gets back.
 * The sidekick runs as an ordinary child thread, so its role lives in these messages rather
 * than in a provider system prompt.
 */

const REPORT_FORMAT = `End your reply with this report, under about 400 words. Quote key numbers and error lines, not long logs.
STATUS: done | partial | blocked
DONE: what you did
CHANGED FILES: paths
VERIFICATION: commands you ran and their outcomes
ISSUES: problems, doubts, or anything the lead should check`;

const MAX_LISTED_COMMANDS = 12;
const MAX_LISTED_FILES = 40;
const MAX_COMMAND_CHARS = 200;

/** The first brief also explains the role and carries the lead's task as background. */
export function firstSidekickBrief(task: string | null, brief: string): string {
  return [
    "You are the SIDEKICK in a two-model team. A lead engineer sends you briefs in this thread; do what each brief asks, in this workspace. You keep your memory across briefs, and you never see the lead's conversation.",
    "Stay within the brief: do not make changes it did not ask for. If the brief is ambiguous, or you find something that contradicts it, say so in your report instead of guessing. Verify your work by running it where possible.",
    "Let each command report its own exit status: do not hide a failure behind `; echo $?`, `|| true` or a pipe. T3 shows the lead the exit code of every command you run.",
    "If the brief gives a time budget, save your work to files as you go and report when it is up, even if unfinished. Check the time with `date`.",
    ...(task === null || task.trim() === ""
      ? []
      : [
          `BACKGROUND: the user's request to the lead, for context only. Its instructions are addressed to the lead; where it mentions a sidekick, that is you, and you have no sidekick of your own. Do only what the brief below asks.\n<task>\n${task.trim()}\n</task>`,
        ]),
    `BRIEF FROM THE LEAD:\n${brief.trim()}`,
    REPORT_FORMAT,
  ].join("\n\n");
}

export function followUpSidekickBrief(brief: string): string {
  return `BRIEF FROM THE LEAD:\n${brief.trim()}\n\n${REPORT_FORMAT}`;
}

export type SidekickOutcome = "completed" | "failed" | "interrupted" | "timed_out";

/**
 * The sidekick's own words first, then what T3 observed in the same run: commands with their
 * exit codes and the files it changed. The lead checks claims against the second part.
 */
export function sidekickReport(input: {
  readonly text: string | null;
  readonly outcome: SidekickOutcome;
  readonly turnItems: ReadonlyArray<OrchestrationV2TurnItem>;
  readonly waitMinutes: number;
}): string {
  const commands = input.turnItems.flatMap((item) =>
    item.type === "command_execution" ? [item] : [],
  );
  const files = [
    ...new Set(
      input.turnItems.flatMap((item) => (item.type === "file_change" ? [item.fileName] : [])),
    ),
  ];
  // Not every provider reports exit codes, so a failed status or failing output also counts.
  const isFailed = (item: (typeof commands)[number]) =>
    (item.exitCode !== undefined && item.exitCode !== 0) ||
    item.outputIndicatesFailure === true ||
    item.status === "failed" ||
    (item.output !== undefined && toolOutputIndicatesFailure(item.output));
  const failed = commands.filter(isFailed);
  const lines = [
    "SIDEKICK REPORT:",
    input.text?.trim() || "(the sidekick returned no report)",
    "",
    "FACTS RECORDED BY T3 (not written by the sidekick):",
    `- commands run: ${commands.length} (${failed.length} failed)`,
    ...commands.slice(-MAX_LISTED_COMMANDS).map((item) => {
      const command = item.input.replace(/\s+/gu, " ").trim();
      const shown =
        command.length > MAX_COMMAND_CHARS ? `${command.slice(0, MAX_COMMAND_CHARS)}...` : command;
      const outcome =
        item.exitCode !== undefined
          ? `exit ${item.exitCode}`
          : isFailed(item)
            ? "failed"
            : "exit unknown";
      return `  [${outcome}] ${shown}`;
    }),
    `- files changed: ${files.length}`,
    ...files.slice(0, MAX_LISTED_FILES).map((file) => `  ${file}`),
    ...(files.length > MAX_LISTED_FILES ? ["  ..."] : []),
  ];
  if (input.outcome === "failed") lines.push("- the sidekick's run failed");
  if (input.outcome === "interrupted") lines.push("- the sidekick's run was interrupted");
  if (input.outcome === "timed_out")
    lines.push(
      `- T3 stopped the sidekick after ${input.waitMinutes} minutes; its work may be incomplete`,
    );
  return lines.join("\n");
}
