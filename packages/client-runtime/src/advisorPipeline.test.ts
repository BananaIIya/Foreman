import { describe, expect, it } from "vite-plus/test";

import {
  ADVISOR_PHASE_LABEL,
  advisorPipelineSummary,
  isAdvisorPipelineBusy,
} from "./advisorPipeline.ts";

describe("isAdvisorPipelineBusy", () => {
  it("locks model and mode changes only while the pipeline runs", () => {
    const busy = Object.keys(ADVISOR_PHASE_LABEL).filter((phase) =>
      isAdvisorPipelineBusy({ phase: phase as keyof typeof ADVISOR_PHASE_LABEL }),
    );
    expect(busy).toEqual(["planning", "paused", "executing"]);
    expect(isAdvisorPipelineBusy(null)).toBe(false);
    expect(isAdvisorPipelineBusy(undefined)).toBe(false);
  });
});

describe("advisorPipelineSummary", () => {
  it("labels a blocked pipeline's summary as the reason", () => {
    expect(advisorPipelineSummary({ phase: "blocked", summary: "Tests fail\n" })).toEqual({
      label: "Blocked reason",
      text: "Tests fail",
    });
  });

  it("shows acceptance results after completion or when replanning", () => {
    expect(advisorPipelineSummary({ phase: "completed", summary: "All passed" })?.label).toBe(
      "Acceptance results",
    );
    expect(advisorPipelineSummary({ phase: "planning", summary: "1 failed" })?.label).toBe(
      "Acceptance results",
    );
  });

  it("hides empty summaries and summaries from an earlier phase", () => {
    expect(advisorPipelineSummary({ phase: "blocked", summary: "  " })).toBeNull();
    expect(advisorPipelineSummary({ phase: "executing", summary: "stale" })).toBeNull();
    expect(advisorPipelineSummary({ phase: "stopped", summary: "stale" })).toBeNull();
    expect(advisorPipelineSummary(null)).toBeNull();
  });
});
