import { useAtomValue } from "@effect/atom-react";
import {
  ADVISOR_PIPELINE_LOCK_HINT,
  selectAdvisorPipelineBusy,
} from "@t3tools/client-runtime/advisor-pipeline";
import type { ScopedThreadRef } from "@t3tools/contracts";
import { Atom } from "effect/reactivity";

import { environmentThreadDetails } from "../../state/threads";

const NO_THREAD_ATOM = Atom.make(null).pipe(Atom.withLabel("advisor-pipeline-lock:none"));

/**
 * Why the composer's model and mode controls are locked, or null. The server
 * rejects those changes while the thread's advisor pipeline runs. Selecting a
 * boolean keeps streaming updates to the thread from rerendering the composer.
 */
export function useAdvisorPipelineLock(ref: ScopedThreadRef | null): string | null {
  const busy = useAtomValue(
    ref === null ? NO_THREAD_ATOM : environmentThreadDetails.threadAtom(ref),
    selectAdvisorPipelineBusy,
  );
  return busy ? ADVISOR_PIPELINE_LOCK_HINT : null;
}
