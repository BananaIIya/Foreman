import { assert, describe, it } from "@effect/vitest";

import { ownedDescendants, parseProcessTable } from "./process-tree.ts";

// runner 100 -> vp 200 -> { vite 300, node --watch 301 -> server 400 }; 500 is unrelated.
const table = parseProcessTable(`
    1     0
  100     1
  200   100
  300   200
  301   200
  400   301
  500     1
  501   500
`);

describe("ownedDescendants", () => {
  it("finds every process below the runner's child", () => {
    assert.sameMembers([...ownedDescendants(table, 200, 100)], [300, 301, 400]);
  });

  it("selects nothing for a pid that is not the runner's own child", () => {
    assert.deepEqual(ownedDescendants(table, 1, 100), []);
    assert.deepEqual(ownedDescendants(table, 500, 100), []);
    assert.deepEqual(ownedDescendants(table, 999, 100), []);
  });
});
