// @effect-diagnostics nodeBuiltinImport:off - reads the live process table at shutdown.
import * as NodeChildProcess from "node:child_process";

export interface ProcessRow {
  readonly pid: number;
  readonly ppid: number;
}

/** Parses `ps -A -o pid=,ppid=` output. */
export function parseProcessTable(text: string): ReadonlyArray<ProcessRow> {
  return text.split("\n").flatMap((line) => {
    const [pid, ppid] = line.trim().split(/\s+/u).map(Number);
    return Number.isInteger(pid) && Number.isInteger(ppid)
      ? [{ pid: pid as number, ppid: ppid as number }]
      : [];
  });
}

/**
 * Every process below `childPid`, but only while `childPid` is still a direct child of
 * `ownPid`. A stale, reused or made-up pid therefore never selects someone else's tree.
 */
export function ownedDescendants(
  rows: ReadonlyArray<ProcessRow>,
  childPid: number,
  ownPid: number,
): ReadonlyArray<number> {
  if (!rows.some((row) => row.pid === childPid && row.ppid === ownPid)) return [];
  const childrenOf = new Map<number, Array<number>>();
  for (const row of rows) {
    childrenOf.set(row.ppid, [...(childrenOf.get(row.ppid) ?? []), row.pid]);
  }
  const found = new Set<number>();
  const pending = [...(childrenOf.get(childPid) ?? [])];
  while (pending.length > 0) {
    const pid = pending.pop() as number;
    if (pid === childPid || pid === ownPid || found.has(pid)) continue;
    found.add(pid);
    pending.push(...(childrenOf.get(pid) ?? []));
  }
  return [...found];
}

/**
 * Signals everything our child `childPid` started, for children such as Vite+ that do not
 * forward signals to their own tasks. POSIX only: Windows has no `ps`.
 */
export function signalOwnedDescendants(childPid: number, signal: NodeJS.Signals): void {
  let table: string;
  try {
    table = NodeChildProcess.execFileSync("ps", ["-A", "-o", "pid=,ppid="], { encoding: "utf8" });
  } catch {
    return;
  }
  for (const pid of ownedDescendants(parseProcessTable(table), childPid, process.pid)) {
    try {
      process.kill(pid, signal);
    } catch {
      // It already exited.
    }
  }
}
