import { useAtomValue } from "@effect/atom-react";
import {
  createEnvironmentThreadDetailAtoms,
  createEnvironmentThreadShellAtoms,
  createEnvironmentThreadStateAtoms,
  EMPTY_ENVIRONMENT_THREAD_STATE,
  type EnvironmentThreadState,
  createThreadEnvironmentAtoms,
} from "@t3tools/client-runtime/state/threads";
import { selectAdvisorPipelineBusy } from "@t3tools/client-runtime/advisor-pipeline";
import type { EnvironmentThread } from "@t3tools/client-runtime/state/models";
import type { EnvironmentId, ThreadId } from "@t3tools/contracts";
import * as Option from "effect/Option";
import { AsyncResult, Atom } from "effect/reactivity";

import { environmentCatalog } from "../connection/catalog";
import { connectionAtomRuntime } from "../connection/runtime";
import { environmentSnapshotAtom } from "./shell";

export const threadEnvironment = createThreadEnvironmentAtoms(
  connectionAtomRuntime,
  environmentSnapshotAtom,
);
const environmentThreads = createEnvironmentThreadStateAtoms(connectionAtomRuntime);
export const environmentThreadDetails = createEnvironmentThreadDetailAtoms(
  environmentThreads.stateAtom,
);
export const environmentThreadShells = createEnvironmentThreadShellAtoms({
  catalogValueAtom: environmentCatalog.catalogValueAtom,
  snapshotAtom: threadEnvironment.snapshotAtom,
});

const EMPTY_THREAD_STATE_ATOM = Atom.make(AsyncResult.success(EMPTY_ENVIRONMENT_THREAD_STATE)).pipe(
  Atom.withLabel("mobile-environment-thread:empty"),
);

export function useEnvironmentThread(
  environmentId: EnvironmentId | null,
  threadId: ThreadId | null,
): EnvironmentThreadState {
  const result = useAtomValue(
    environmentId !== null && threadId !== null
      ? environmentThreads.stateAtom(environmentId, threadId)
      : EMPTY_THREAD_STATE_ATOM,
  );
  const state = Option.getOrElse(
    AsyncResult.value(result),
    () => EMPTY_ENVIRONMENT_THREAD_STATE,
  ) as EnvironmentThreadState;
  return state;
}

const NO_THREAD_ATOM = Atom.make<EnvironmentThread | null>(null).pipe(
  Atom.withLabel("mobile-environment-thread:none"),
);

/**
 * True while the thread's advisor pipeline runs, when the server refuses model
 * and mode changes. Selecting a boolean keeps streaming from rerendering readers.
 */
export function useAdvisorPipelineBusy(
  environmentId: EnvironmentId | null,
  threadId: ThreadId | null,
): boolean {
  return useAtomValue(
    environmentId !== null && threadId !== null
      ? environmentThreadDetails.threadAtom({ environmentId, threadId })
      : NO_THREAD_ATOM,
    selectAdvisorPipelineBusy,
  );
}
