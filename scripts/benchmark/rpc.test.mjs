import { test, vi } from "vite-plus/test";
import * as NodeAssert from "node:assert/strict";
import { T3Rpc } from "./rpc.mjs";

test("failed WebSocket handshake rejects promptly without leaking a pending RPC", async () => {
  class FailedSocket extends EventTarget {
    static OPEN = 1;
    readyState = 0;
    constructor() {
      super();
      queueMicrotask(() => this.dispatchEvent(new Event("error")));
    }
    close() {
      this.readyState = 3;
      this.dispatchEvent(new Event("close"));
    }
  }
  vi.stubGlobal("fetch", async () => ({
    ok: true,
    json: async () => ({ ticket: "fixture-ticket" }),
  }));
  vi.stubGlobal("WebSocket", FailedSocket);
  const client = new T3Rpc("http://localhost:13773", "fixture-token");
  try {
    await NodeAssert.rejects(client.connect(), /connection failed/);
    await NodeAssert.rejects(client.rpc("server.getConfig", {}), /disconnected/);
    NodeAssert.equal(client.pending.size, 0);
  } finally {
    client.close();
    vi.unstubAllGlobals();
  }
});

test("disconnect rejects an outstanding command and removes its timeout", async () => {
  class ClosingSocket extends EventTarget {
    static OPEN = 1;
    readyState = 1;
    constructor() {
      super();
      queueMicrotask(() => this.dispatchEvent(new Event("open")));
    }
    send() {
      queueMicrotask(() => this.close());
    }
    close() {
      this.readyState = 3;
      this.dispatchEvent(new Event("close"));
    }
  }
  vi.stubGlobal("fetch", async () => ({
    ok: true,
    json: async () => ({ ticket: "fixture-ticket" }),
  }));
  vi.stubGlobal("WebSocket", ClosingSocket);
  const client = new T3Rpc("http://localhost:13773", "fixture-token");
  try {
    await client.connect();
    await NodeAssert.rejects(client.rpc("server.getConfig", {}), /disconnected/);
    NodeAssert.equal(client.pending.size, 0);
  } finally {
    client.close();
    vi.unstubAllGlobals();
  }
});
