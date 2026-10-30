// Keep recent thread snapshots for back navigation. Live subscriptions end
// when the last detail consumer leaves.
export const THREAD_SNAPSHOT_IDLE_TTL_MS = 5 * 60_000;

const THREAD_SNAPSHOT_IDLE_MAX_BYTES = 32 * 1024 * 1024;
const THREAD_SNAPSHOT_IDLE_MAX_ENTRIES = 32;

export interface ThreadSnapshotRetentionOptions {
  readonly maxBytes?: number;
  readonly maxEntries?: number;
}

/** Estimate retained JS data without encoding another copy of a large snapshot.
 * Shared item objects are counted once. Stop once the entry cannot fit anyway.
 * This is a cache budget, not a measurement of the engine's heap layout.
 */
export function estimateThreadSnapshotBytes(snapshot: unknown, limit = Infinity): number {
  const seen = new Set<object>();
  const pending: unknown[] = [snapshot];
  let bytes = 0;
  while (pending.length > 0 && bytes <= limit) {
    const value = pending.pop();
    if (typeof value === "string") {
      bytes += value.length * 2;
    } else if (typeof value === "object" && value !== null && !seen.has(value)) {
      seen.add(value);
      bytes += 32;
      if (Array.isArray(value)) {
        bytes += value.length * 8;
        if (bytes > limit) break;
        for (const item of value) pending.push(item);
      } else {
        for (const key of Object.keys(value)) {
          bytes += key.length * 2 + 8;
          pending.push((value as Record<string, unknown>)[key]);
        }
      }
    } else if (typeof value === "number" || typeof value === "bigint") {
      bytes += 8;
    }
  }
  return bytes;
}

/** Only idle scopes enter this LRU. Active views and desktop keep-alives retain
 * their complete history; an evicted idle entry reloads through the normal path.
 */
export function createThreadSnapshotRetention<Cache extends { snapshot: unknown | undefined }>(
  options: ThreadSnapshotRetentionOptions = {},
) {
  const maxBytes = options.maxBytes ?? THREAD_SNAPSHOT_IDLE_MAX_BYTES;
  const maxEntries = options.maxEntries ?? THREAD_SNAPSHOT_IDLE_MAX_ENTRIES;
  const idle = new Map<Cache, number>();
  let bytes = 0;
  const activate = (cache: Cache) => {
    const size = idle.get(cache);
    if (size === undefined) return;
    idle.delete(cache);
    bytes -= size;
  };
  return {
    activate,
    retain: (cache: Cache) => {
      activate(cache);
      if (cache.snapshot === undefined) return;
      const size = estimateThreadSnapshotBytes(cache.snapshot, maxBytes);
      if (size > maxBytes || maxEntries === 0) {
        cache.snapshot = undefined;
        return;
      }
      idle.set(cache, size);
      bytes += size;
      for (const [oldest, oldestSize] of idle) {
        if (bytes <= maxBytes && idle.size <= maxEntries) break;
        idle.delete(oldest);
        bytes -= oldestSize;
        oldest.snapshot = undefined;
      }
    },
  };
}
