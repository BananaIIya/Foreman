import * as NodeEvents from "node:events";

export class T3Rpc extends NodeEvents.EventEmitter {
  constructor(baseUrl, token) {
    super();
    this.baseUrl = baseUrl;
    this.token = token;
    this.sequence = 0;
    this.pending = new Map();
    this.watches = new Map();
    this.states = new Map();
  }
  async connect() {
    const response = await fetch(new URL("/api/auth/websocket-ticket", this.baseUrl), {
      method: "POST",
      headers: { Authorization: "Bearer " + this.token },
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) throw new Error("WebSocket ticket HTTP " + response.status);
    const { ticket } = await response.json();
    const url = new URL("/ws?orchestrationProtocol=2", this.baseUrl);
    url.searchParams.set("wsTicket", ticket);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    this.socket = new WebSocket(url);
    this.socket.addEventListener("message", (event) => {
      const frame = JSON.parse(event.data);
      for (const message of Array.isArray(frame) ? frame : [frame]) this.receive(message);
    });
    this.socket.addEventListener("close", () => {
      for (const request of this.pending.values()) {
        clearTimeout(request.timeout);
        request.reject(new Error("RPC disconnected"));
      }
      this.pending.clear();
      this.emit("disconnected");
    });
    await new Promise((resolve, reject) => {
      const clean = () => {
        clearTimeout(timeout);
        this.socket.removeEventListener("open", opened);
        this.socket.removeEventListener("error", failed);
        this.socket.removeEventListener("close", failed);
      };
      const opened = () => {
        clean();
        resolve();
      };
      const failed = () => {
        clean();
        reject(new Error("WebSocket connection failed"));
      };
      const timeout = setTimeout(() => {
        failed();
        this.socket.close();
      }, 15_000);
      this.socket.addEventListener("open", opened, { once: true });
      this.socket.addEventListener("error", failed, { once: true });
      this.socket.addEventListener("close", failed, { once: true });
    });
    return this;
  }
  send(tag, payload, id = String(++this.sequence)) {
    this.socket.send(JSON.stringify({ _tag: "Request", id, tag, payload, headers: [] }));
    return id;
  }
  receive(message) {
    if (message._tag === "Chunk") {
      this.socket.send(JSON.stringify({ _tag: "Ack", requestId: message.requestId }));
      const threadId = this.watches.get(String(message.requestId));
      if (!threadId) return;
      for (const item of message.values) {
        if (item.kind === "snapshot") this.states.set(threadId, item.projection);
        if (item.kind === "event") {
          const state = this.states.get(threadId);
          if (state) {
            if (item.event.type.startsWith("thread.")) state.thread = item.event.payload;
            if (["run.created", "run.updated"].includes(item.event.type)) {
              const index = state.runs.findIndex((run) => run.id === item.event.payload.id);
              if (index < 0) state.runs.push(item.event.payload);
              else state.runs[index] = item.event.payload;
            }
          }
        }
        this.emit("state", threadId, this.states.get(threadId), item);
      }
    }
    if (message._tag === "Exit") {
      const request = this.pending.get(String(message.requestId));
      if (!request) return;
      this.pending.delete(String(message.requestId));
      clearTimeout(request.timeout);
      if (message.exit._tag === "Success") request.resolve(message.exit.value);
      else request.reject(new Error(JSON.stringify(message.exit.cause)));
    }
  }
  rpc(tag, payload) {
    if (this.socket?.readyState !== WebSocket.OPEN)
      return Promise.reject(new Error("RPC disconnected"));
    return new Promise((resolve, reject) => {
      const id = String(++this.sequence);
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("RPC timeout: " + tag));
      }, 60_000);
      this.pending.set(id, { resolve, reject, timeout });
      this.send(tag, payload, id);
    });
  }
  watch(threadId) {
    if ([...this.watches.values()].includes(threadId)) return;
    const id = String(++this.sequence);
    this.watches.set(id, threadId);
    this.send("orchestration.subscribeThread", { threadId, requestCompletionMarker: true }, id);
  }
  waitFor(threadId, predicate, timeoutMs = 600_000) {
    return new Promise((resolve, reject) => {
      const clean = () => {
        clearTimeout(timeout);
        this.off("state", check);
        this.off("disconnected", disconnected);
      };
      const check = () => {
        const state = this.states.get(threadId);
        if (state && predicate(state)) {
          clean();
          resolve(state);
        }
      };
      const disconnected = () => {
        clean();
        reject(new Error("RPC disconnected"));
      };
      const timeout = setTimeout(() => {
        clean();
        reject(new Error("Trial deadline exceeded"));
      }, timeoutMs);
      this.on("state", check);
      this.on("disconnected", disconnected);
      check();
    });
  }
  async projection(threadId) {
    const response = await fetch(new URL("/api/orchestration/threads/" + threadId, this.baseUrl), {
      headers: { Authorization: "Bearer " + this.token, "x-t3-orchestration-protocol": "2" },
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) throw new Error("Projection HTTP " + response.status);
    return (await response.json()).projection;
  }
  close() {
    for (const request of this.pending.values()) clearTimeout(request.timeout);
    this.socket?.close();
  }
}
