import { useAtomValue } from "@effect/atom-react";
import {
  advisorModelOptions,
  sidekickModelOptions,
} from "@t3tools/client-runtime/advisor-model-options";
import {
  ADVISOR_PHASE_LABEL,
  advisorPipelineSummary,
  isAdvisorPipelineBusy,
} from "@t3tools/client-runtime/advisor-pipeline";
import { randomUUID } from "../../lib/utils";
import {
  CommandId,
  type EnvironmentId,
  type OrchestrationV2ThreadProjection,
  type ServerProvider,
  type AdvisorConfiguration,
  type SidekickSelection,
  type ThreadId,
} from "@t3tools/contracts";
import { useState } from "react";
import { threadEnvironment } from "../../state/threads";
import { useOrchestrationCommand } from "../../state/use-orchestration-command";
import { Button } from "../ui/button";
import { Switch } from "../ui/switch";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "../ui/select";
import ChatMarkdown from "../ChatMarkdown";
import { MarkdownThreadLink } from "./MarkdownThreadLink";

const MODE_LABEL = {
  off: "Single model",
  sidekick: "Lead + sidekick",
  advisor: "Advisor (legacy)",
};
type MultiModelMode = keyof typeof MODE_LABEL;

export function AdvisorPipelinePanel(props: {
  environmentId: EnvironmentId;
  projection?: OrchestrationV2ThreadProjection;
  draft?: {
    threadId: ThreadId;
    advisor: AdvisorConfiguration | null;
    sidekick: SidekickSelection | null;
    onChange: (change: {
      advisor: AdvisorConfiguration | null;
      sidekick: SidekickSelection | null;
    }) => void;
  };
  providers: ReadonlyArray<ServerProvider>;
  canOperate: boolean;
}) {
  const thread = props.projection?.thread ?? {
    id: props.draft!.threadId,
    advisor: props.draft!.advisor,
    advisorState: null,
    sidekick: props.draft!.sidekick ? { ...props.draft!.sidekick, threadId: null } : null,
    worktreePath: null,
  };
  const permission = useAtomValue(threadEnvironment.setAdvisor.permissionAtom(props.environmentId));
  const canOperate = props.canOperate && permission;
  const state = thread.advisorState;
  const busy = isAdvisorPipelineBusy(state);
  const summary = advisorPipelineSummary(state);
  const [pending, setPending] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setAdvisor = useOrchestrationCommand(threadEnvironment.setAdvisor, {
    reportFailure: false,
  });
  const resume = useOrchestrationCommand(threadEnvironment.resumeAdvisorPlan, {
    reportFailure: false,
  });
  const cancel = useOrchestrationCommand(threadEnvironment.cancelAdvisorPipeline, {
    reportFailure: false,
  });
  const setSidekick = useOrchestrationCommand(threadEnvironment.setSidekick, {
    reportFailure: false,
  });
  const sidekickChoices = sidekickModelOptions(props.providers, thread.sidekick?.modelSelection);
  const selectedSidekick = thread.sidekick
    ? JSON.stringify([
        thread.sidekick.modelSelection.instanceId,
        thread.sidekick.modelSelection.model,
      ])
    : null;
  const mode: MultiModelMode = thread.sidekick ? "sidekick" : thread.advisor ? "advisor" : "off";
  const { options, fallback } = advisorModelOptions(
    props.providers,
    thread.advisor?.modelSelection,
  );
  const selected = thread.advisor
    ? JSON.stringify([
        thread.advisor.modelSelection.instanceId,
        thread.advisor.modelSelection.model,
      ])
    : null;
  /** Advisor mode and a sidekick exclude each other, so a change clears the other first. */
  const configure = async (next: {
    advisor: AdvisorConfiguration | null;
    sidekick: SidekickSelection | null;
  }) => {
    if (pending) return;
    if (props.draft) {
      props.draft.onChange(next);
      return;
    }
    setPending(true);
    setError(null);
    const sendAdvisor = () =>
      setAdvisor({
        environmentId: props.environmentId,
        input: {
          type: "thread.advisor.set",
          commandId: CommandId.make(randomUUID()),
          threadId: thread.id,
          advisor: next.advisor,
        },
      });
    const sendSidekick = () =>
      setSidekick({
        environmentId: props.environmentId,
        input: {
          type: "thread.sidekick.set",
          commandId: CommandId.make(randomUUID()),
          threadId: thread.id,
          sidekick: next.sidekick,
        },
      });
    const steps = [
      ...(next.sidekick === null && thread.sidekick ? [sendSidekick] : []),
      ...(next.advisor === null && thread.advisor ? [sendAdvisor] : []),
      ...(next.advisor !== null ? [sendAdvisor] : []),
      ...(next.sidekick !== null ? [sendSidekick] : []),
    ];
    for (const step of steps) {
      const result = await step();
      if (result._tag === "Failure") {
        setPending(false);
        setError(
          "Could not change multi-model settings. Finish or stop the current turn and try again.",
        );
        return;
      }
    }
    setPending(false);
  };
  const update = (advisor: AdvisorConfiguration | null) => configure({ advisor, sidekick: null });
  const chooseMode = (value: MultiModelMode) => {
    if (value === mode) return;
    if (value === "off") void configure({ advisor: null, sidekick: null });
    if (value === "advisor" && fallback)
      void update({ modelSelection: fallback.selection, strategy: "worker-first" });
    if (value === "sidekick" && sidekickChoices.fallback)
      void configure({
        advisor: null,
        sidekick: { modelSelection: sidekickChoices.fallback.selection },
      });
  };
  return (
    <section
      aria-label="Advisor pipeline"
      className="shrink-0 border-b border-border bg-muted/20 px-4 py-2"
    >
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <div className="max-w-full sm:w-44">
          <Select
            value={mode}
            items={(Object.keys(MODE_LABEL) as MultiModelMode[]).map((value) => ({
              value,
              label: MODE_LABEL[value],
            }))}
            disabled={!canOperate || busy || pending}
            onValueChange={(value) => chooseMode(value as MultiModelMode)}
          >
            <SelectTrigger aria-label="Multi-model mode" size="sm">
              <SelectValue>{MODE_LABEL[mode]}</SelectValue>
            </SelectTrigger>
            <SelectPopup>
              <SelectItem value="off">{MODE_LABEL.off}</SelectItem>
              <SelectItem value="sidekick" disabled={!sidekickChoices.fallback}>
                {MODE_LABEL.sidekick}
              </SelectItem>
              <SelectItem value="advisor" disabled={!fallback}>
                {MODE_LABEL.advisor}
              </SelectItem>
            </SelectPopup>
          </Select>
        </div>
        {thread.sidekick ? (
          <>
            <div className="max-w-full sm:w-64">
              <Select
                value={selectedSidekick}
                items={sidekickChoices.options}
                disabled={!canOperate || pending}
                onValueChange={(value) => {
                  const option = sidekickChoices.options.find((entry) => entry.value === value);
                  if (option)
                    void configure({
                      advisor: null,
                      sidekick: { modelSelection: option.selection },
                    });
                }}
              >
                <SelectTrigger aria-label="Sidekick model" size="sm">
                  <SelectValue>
                    {sidekickChoices.options.find((option) => option.value === selectedSidekick)
                      ?.label ?? thread.sidekick.modelSelection.model}
                  </SelectValue>
                </SelectTrigger>
                <SelectPopup>
                  {sidekickChoices.options.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectPopup>
              </Select>
            </div>
            {thread.sidekick.threadId ? (
              <span className="text-xs">
                <MarkdownThreadLink
                  environmentId={props.environmentId}
                  threadId={thread.sidekick.threadId}
                  label="Sidekick thread"
                />
              </span>
            ) : null}
          </>
        ) : thread.advisor ? (
          <>
            <div className="max-w-full sm:w-64">
              <Select
                value={selected}
                items={options}
                disabled={!canOperate || busy || pending}
                onValueChange={(value) => {
                  const option = options.find((entry) => entry.value === value);
                  if (option && thread.advisor)
                    void update({ ...thread.advisor, modelSelection: option.selection });
                }}
              >
                <SelectTrigger aria-label="Advisor model" size="sm">
                  <SelectValue>
                    {options.find((option) => option.value === selected)?.label ??
                      thread.advisor.modelSelection.model}
                  </SelectValue>
                </SelectTrigger>
                <SelectPopup>
                  {options.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectPopup>
              </Select>
            </div>
            <label className="flex items-center gap-2 text-xs">
              <Switch
                aria-label="Plan before worker"
                size="sm"
                checked={thread.advisor.strategy === "plan-first"}
                disabled={!canOperate || busy || pending}
                onCheckedChange={(planFirst) => {
                  if (thread.advisor)
                    void update({
                      ...thread.advisor,
                      strategy: planFirst ? "plan-first" : "worker-first",
                    });
                }}
              />
              Plan before worker
            </label>
            {busy ? (
              <Button
                size="xs"
                variant="outline"
                disabled={!canOperate || pending}
                onClick={async () => {
                  setPending(true);
                  setError(null);
                  const result = await cancel({
                    environmentId: props.environmentId,
                    input: {
                      type: "advisor.cancel",
                      commandId: CommandId.make(randomUUID()),
                      threadId: thread.id,
                    },
                  });
                  setPending(false);
                  if (result._tag === "Failure")
                    setError("Could not stop the pipeline. Try again.");
                }}
              >
                Stop pipeline
              </Button>
            ) : null}
          </>
        ) : (
          <span className="text-xs text-muted-foreground">
            Add a cheaper sidekick model that the composer model can hand work to.
          </span>
        )}
      </div>
      {thread.sidekick ? (
        <p className="mt-2 text-xs text-muted-foreground">
          The composer model leads and reviews. It hands well-scoped work, such as exploring,
          running tests or implementing what it specified, to the sidekick, which keeps its memory
          across briefs. Small tasks it does itself.
        </p>
      ) : null}
      {thread.advisor ? (
        <p className="mt-2 text-xs text-muted-foreground">
          {thread.advisor.strategy === "plan-first"
            ? "Get a concise plan first, then the worker automatically implements it."
            : "The worker starts immediately, asks the advisor after a failure or blocker, and automatically continues with the plan."}
        </p>
      ) : null}
      {thread.advisor && state ? (
        <div className="mt-2 space-y-2 text-xs">
          <div role="status" className="flex flex-wrap items-center gap-3">
            <span>{ADVISOR_PHASE_LABEL[state.phase]}</span>
            <span className="text-muted-foreground">
              Worker failures: {state.failures}
              {state.plan ? "/3" : ""} · Replans: {state.cycle}/3
            </span>
            {/* Only historical tasks can be paused; new plans continue automatically. */}
            {state.phase === "paused" && state.currentRunId ? (
              <Button
                size="xs"
                disabled={!canOperate || pending}
                onClick={async () => {
                  setPending(true);
                  const result = await resume({
                    environmentId: props.environmentId,
                    input: {
                      type: "advisor.resume",
                      commandId: CommandId.make(randomUUID()),
                      threadId: thread.id,
                      expectedRunId: state.currentRunId!,
                    },
                  });
                  setPending(false);
                  if (result._tag === "Failure") setError("This task is no longer paused.");
                }}
              >
                Continue task
              </Button>
            ) : null}
          </div>
          {state.plan ? (
            <details
              key={state.plan.path}
              onToggle={(event) => setPlanOpen(event.currentTarget.open)}
            >
              <summary className="cursor-pointer font-medium">
                plan.md · {state.plan.files.length} files · {state.plan.commands.length} checks
              </summary>
              {planOpen ? (
                <div className="mt-2 max-h-60 overflow-auto rounded-md border border-border p-3">
                  <ChatMarkdown
                    text={state.plan.markdown}
                    cwd={thread.worktreePath ?? undefined}
                    threadRef={{ environmentId: props.environmentId, threadId: thread.id }}
                  />
                </div>
              ) : null}
            </details>
          ) : null}
          {summary ? (
            <details open={state.phase === "blocked"}>
              <summary className="cursor-pointer">{summary.label}</summary>
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs">
                {summary.text}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </section>
  );
}
