import { describe, expect, it } from "vite-plus/test";

import { createThreadSnapshotRetention, estimateThreadSnapshotBytes } from "./threadRetention.ts";

const entry = (text = "retained") => ({ snapshot: { text } as { text: string } | undefined });

describe("thread snapshot retention", () => {
  it("evicts the least recently used idle entry and leaves active snapshots untouched", () => {
    const retention = createThreadSnapshotRetention<ReturnType<typeof entry>>({ maxEntries: 2 });
    const active = entry("active");
    const first = entry("first");
    const second = entry("second");
    const third = entry("third");
    retention.retain(active);
    retention.activate(active);
    retention.retain(first);
    retention.retain(second);
    retention.activate(first);
    retention.retain(first);
    retention.retain(third);
    expect(active.snapshot?.text).toBe("active");
    expect(first.snapshot?.text).toBe("first");
    expect(second.snapshot).toBeUndefined();
    expect(third.snapshot?.text).toBe("third");
  });

  it("bounds idle bytes and does not flush useful entries for an oversized snapshot", () => {
    const size = estimateThreadSnapshotBytes(entry().snapshot);
    const retention = createThreadSnapshotRetention<ReturnType<typeof entry>>({
      maxBytes: size * 2,
    });
    const first = entry();
    const second = entry();
    const third = entry();
    retention.retain(first);
    retention.retain(second);
    retention.retain(third);
    expect(first.snapshot).toBeUndefined();
    const oversized = entry("x".repeat(size * 2));
    retention.retain(oversized);
    expect(oversized.snapshot).toBeUndefined();
    expect(second.snapshot).toBeDefined();
    expect(third.snapshot).toBeDefined();
  });

  it("releases accounting when an entry expires or becomes active", () => {
    const retention = createThreadSnapshotRetention<ReturnType<typeof entry>>({ maxEntries: 1 });
    const first = entry();
    retention.retain(first);
    retention.activate(first);
    retention.activate(first);
    const second = entry();
    retention.retain(second);
    expect(first.snapshot).toBeDefined();
    expect(second.snapshot).toBeDefined();
  });

  it("counts shared objects once and stops once a snapshot cannot fit", () => {
    const shared = { text: "x".repeat(1000) };
    expect(estimateThreadSnapshotBytes([shared, shared])).toBeLessThan(
      estimateThreadSnapshotBytes([shared, { ...shared }]),
    );
    const cycle: { self?: unknown; text: string } = { text: "cycle" };
    cycle.self = cycle;
    expect(estimateThreadSnapshotBytes(cycle)).toBeGreaterThan(0);
    expect(estimateThreadSnapshotBytes([shared, shared], 10)).toBeGreaterThan(10);
  });
});
