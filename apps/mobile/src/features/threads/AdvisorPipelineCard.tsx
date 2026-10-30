import { useAtomValue } from "@effect/atom-react";
import {
  advisorModelOptions,
  sidekickModelOptions,
} from "@t3tools/client-runtime/advisor-model-options";
import {
  ADVISOR_PHASE_LABEL,
  ADVISOR_PIPELINE_LOCK_HINT,
  advisorPipelineSummary,
  isAdvisorPipelineBusy,
} from "@t3tools/client-runtime/advisor-pipeline";
import { StackActions, useNavigation } from "@react-navigation/native";
import {
  CommandId,
  type EnvironmentId,
  type ServerProvider,
  type SidekickSelection,
  type ThreadId,
} from "@t3tools/contracts";
import * as Option from "effect/Option";
import { useState } from "react";
import { Pressable, ScrollView, Switch, View } from "react-native";
import { AppText as Text } from "../../components/AppText";
import { uuidv4 } from "../../lib/uuid";
import { threadEnvironment, useEnvironmentThread } from "../../state/threads";
import { useAtomCommand } from "../../state/use-atom-command";
import { ThreadUsageSummary } from "./ThreadUsageSummary";

export function AdvisorPipelineCard(props: {
  environmentId: EnvironmentId;
  threadId: ThreadId;
  providers: ReadonlyArray<ServerProvider>;
  canOperate: boolean;
}) {
  const detail = useEnvironmentThread(props.environmentId, props.threadId);
  const projection = Option.getOrNull(detail.data);
  const canOperate =
    useAtomValue(threadEnvironment.setAdvisor.permissionAtom(props.environmentId)) &&
    props.canOperate;
  const configure = useAtomCommand(threadEnvironment.setAdvisor);
  const resume = useAtomCommand(threadEnvironment.resumeAdvisorPlan);
  const cancel = useAtomCommand(threadEnvironment.cancelAdvisorPipeline);
  const configureSidekick = useAtomCommand(threadEnvironment.setSidekick);
  const [expanded, setExpanded] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [choosingSidekick, setChoosingSidekick] = useState(false);
  const [pending, setPending] = useState(false);
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [usageExpanded, setUsageExpanded] = useState(false);
  const navigation = useNavigation();
  if (!projection) return null;
  const { thread } = projection;
  const state = thread.advisorState;
  const busy = isAdvisorPipelineBusy(state);
  const summary = advisorPipelineSummary(state);
  const sidekickThreadId = thread.sidekick?.threadId ?? null;
  const { options, fallback } = advisorModelOptions(
    props.providers,
    thread.advisor?.modelSelection,
  );
  const sidekickChoices = sidekickModelOptions(props.providers, thread.sidekick?.modelSelection);
  const sendSidekick = (sidekick: SidekickSelection | null) =>
    configureSidekick({
      environmentId: props.environmentId,
      input: {
        type: "thread.sidekick.set",
        commandId: CommandId.make(uuidv4()),
        threadId: thread.id,
        sidekick,
      },
    });
  // Advisor mode and a sidekick exclude each other, so turning one on clears the other first.
  const updateSidekick = async (sidekick: SidekickSelection | null) => {
    setPending(true);
    if (sidekick && thread.advisor) await update(null);
    await sendSidekick(sidekick);
    setPending(false);
    setChoosingSidekick(false);
  };
  const update = async (advisor: typeof thread.advisor) => {
    setPending(true);
    if (advisor && thread.sidekick) await sendSidekick(null);
    await configure({
      environmentId: props.environmentId,
      input: {
        type: "thread.advisor.set",
        commandId: CommandId.make(uuidv4()),
        threadId: thread.id,
        advisor: advisor ?? null,
      },
    });
    setPending(false);
    setChoosing(false);
  };
  return (
    <View className="gap-2 border-b border-edge bg-surface px-4 py-2">
      <View className="flex-row items-center justify-between">
        <Text className="text-sm text-foreground">Sidekick</Text>
        <Switch
          accessibilityLabel="Sidekick"
          value={!!thread.sidekick}
          disabled={
            !canOperate || pending || busy || (!thread.sidekick && !sidekickChoices.fallback)
          }
          onValueChange={(enabled) => {
            void updateSidekick(
              enabled && sidekickChoices.fallback
                ? { modelSelection: sidekickChoices.fallback.selection }
                : null,
            );
          }}
        />
      </View>
      {thread.sidekick ? (
        <>
          <Pressable
            accessibilityRole="button"
            disabled={!canOperate || pending}
            onPress={() => setChoosingSidekick(!choosingSidekick)}
          >
            <Text className="text-xs text-foreground-muted">
              Sidekick: {thread.sidekick.modelSelection.model}
            </Text>
          </Pressable>
          {choosingSidekick ? (
            <ScrollView className="max-h-32">
              {sidekickChoices.options.map((option) => (
                <Pressable
                  key={option.value}
                  onPress={() => void updateSidekick({ modelSelection: option.selection })}
                >
                  <Text className="py-2 text-sm text-foreground">{option.label}</Text>
                </Pressable>
              ))}
            </ScrollView>
          ) : null}
          <Text className="text-xs text-foreground-muted">
            The thread's model leads and reviews, and hands well-scoped work to the sidekick, which
            keeps its memory across briefs.
          </Text>
          {sidekickThreadId ? (
            <Pressable
              accessibilityRole="link"
              accessibilityHint="Opens the sidekick's thread"
              onPress={() =>
                // Push, not navigate: navigate reuses this Thread route, so back
                // would skip the lead thread.
                navigation.dispatch(
                  StackActions.push("Thread", {
                    environmentId: String(props.environmentId),
                    threadId: String(sidekickThreadId),
                  }),
                )
              }
            >
              <Text className="py-1 text-xs text-primary">Open sidekick thread</Text>
            </Pressable>
          ) : null}
        </>
      ) : null}
      <View className="flex-row items-center justify-between">
        <Text className="text-sm text-foreground">Advisor mode</Text>
        <Switch
          accessibilityLabel="Advisor mode"
          value={!!thread.advisor}
          disabled={!canOperate || pending || busy || (!thread.advisor && !fallback)}
          onValueChange={(enabled) => {
            void update(
              enabled && fallback
                ? {
                    modelSelection: fallback.selection,
                    strategy: "worker-first",
                  }
                : null,
            );
          }}
        />
      </View>
      {thread.advisor ? (
        <>
          <Pressable
            accessibilityRole="button"
            disabled={!canOperate || pending || busy}
            onPress={() => setChoosing(!choosing)}
          >
            <Text className="text-xs text-foreground-muted">
              Advisor: {thread.advisor.modelSelection.model}
            </Text>
          </Pressable>
          {choosing ? (
            <ScrollView className="max-h-32">
              {options.map((option) => (
                <Pressable
                  key={option.value}
                  onPress={() => {
                    if (thread.advisor)
                      void update({ ...thread.advisor, modelSelection: option.selection });
                  }}
                >
                  <Text className="py-2 text-sm text-foreground">{option.label}</Text>
                </Pressable>
              ))}
            </ScrollView>
          ) : null}
          <View className="flex-row items-center justify-between">
            <Text className="text-xs text-foreground-muted">Plan before worker</Text>
            <Switch
              accessibilityLabel="Plan before worker"
              value={thread.advisor.strategy === "plan-first"}
              disabled={!canOperate || pending || busy}
              onValueChange={(planFirst) => {
                if (thread.advisor)
                  void update({
                    ...thread.advisor,
                    strategy: planFirst ? "plan-first" : "worker-first",
                  });
              }}
            />
          </View>
          <Text className="text-xs text-foreground-muted">
            {thread.advisor.strategy === "plan-first"
              ? "Get a concise plan first, then the worker automatically implements it."
              : "Worker starts immediately, asks the advisor after failure or a blocker, and automatically continues."}
          </Text>
          {state ? (
            <View accessibilityLiveRegion="polite">
              <Text className="text-sm text-foreground">{ADVISOR_PHASE_LABEL[state.phase]}</Text>
              <Text className="text-xs text-foreground-muted">
                Worker failures {state.failures}
                {state.plan ? "/3" : ""} · Replans {state.cycle}/3
              </Text>
            </View>
          ) : null}
          {busy ? (
            <Text className="text-xs text-foreground-muted">{ADVISOR_PIPELINE_LOCK_HINT}</Text>
          ) : null}
          {summary ? (
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ expanded: summaryExpanded }}
              onPress={() => setSummaryExpanded(!summaryExpanded)}
            >
              <Text className="text-xs text-foreground">{summary.label}</Text>
              {summaryExpanded ? null : (
                <Text numberOfLines={3} className="text-xs text-foreground-muted">
                  {summary.text}
                </Text>
              )}
            </Pressable>
          ) : null}
          {summary && summaryExpanded ? (
            <ScrollView className="max-h-48">
              <Text selectable className="text-xs text-foreground">
                {summary.text}
              </Text>
            </ScrollView>
          ) : null}
          {state?.plan ? (
            <Pressable accessibilityRole="button" onPress={() => setExpanded(!expanded)}>
              <Text className="text-sm text-foreground">
                {expanded ? "Hide" : "Show"} plan.md · {state.plan.files.length} files ·{" "}
                {state.plan.commands.length} checks
              </Text>
            </Pressable>
          ) : null}
          {expanded && state?.plan ? (
            <ScrollView className="max-h-48">
              <Text selectable className="text-xs text-foreground">
                {state.plan.markdown}
              </Text>
            </ScrollView>
          ) : null}
          {/* Only historical tasks can be paused; new plans continue automatically. */}
          {state?.phase === "paused" && state.currentRunId ? (
            <Pressable
              accessibilityRole="button"
              disabled={!canOperate || pending}
              onPress={() => {
                void resume({
                  environmentId: props.environmentId,
                  input: {
                    type: "advisor.resume",
                    commandId: CommandId.make(uuidv4()),
                    threadId: thread.id,
                    expectedRunId: state.currentRunId!,
                  },
                });
              }}
            >
              <Text className="py-2 text-sm text-primary">Continue task</Text>
            </Pressable>
          ) : null}
          {busy ? (
            <Pressable
              accessibilityRole="button"
              disabled={!canOperate || pending}
              onPress={() => {
                void cancel({
                  environmentId: props.environmentId,
                  input: {
                    type: "advisor.cancel",
                    commandId: CommandId.make(uuidv4()),
                    threadId: thread.id,
                  },
                });
              }}
            >
              <Text className="py-1 text-xs text-destructive">Stop pipeline</Text>
            </Pressable>
          ) : null}
        </>
      ) : null}
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: usageExpanded }}
        onPress={() => setUsageExpanded(!usageExpanded)}
      >
        <Text className="text-xs text-foreground-muted">
          {usageExpanded ? "Hide" : "Show"} thread cost
        </Text>
      </Pressable>
      {usageExpanded ? (
        <ThreadUsageSummary
          environmentId={props.environmentId}
          threadIds={sidekickThreadId ? [thread.id, sidekickThreadId] : [thread.id]}
        />
      ) : null}
    </View>
  );
}
