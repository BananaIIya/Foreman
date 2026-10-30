import { describe, expect, it } from "vite-plus/test";
import { MessageId, NodeId, ProviderDriverKind, ThreadId, TurnItemId } from "@t3tools/contracts";
import * as DateTime from "effect/DateTime";
import { makeAssistantStreamingFilter, splitBufferedAssistantText } from "./assistantStreaming.ts";
import type * as ProviderAdapter from "@t3tools/provider-core/server/ProviderAdapter";

const message = (text: string, streaming = true): ProviderAdapter.ProviderAdapterV2Event => ({
  type: "message.updated",
  driver: ProviderDriverKind.make("codex"),
  message: {
    id: MessageId.make("message"),
    threadId: ThreadId.make("thread"),
    runId: null,
    nodeId: null,
    createdBy: "agent",
    creationSource: "provider",
    updatedAt: DateTime.makeUnsafe("2026-09-14T00:00:00Z"),
    role: "assistant",
    text,
    attachments: [],
    createdAt: DateTime.makeUnsafe("2026-09-14T00:00:00Z"),
    streaming,
  },
});

const turnItem = (
  text: string,
  streaming = true,
  type: "assistant_message" | "reasoning" = "assistant_message",
): ProviderAdapter.ProviderAdapterV2Event => ({
  type: "turn_item.updated",
  driver: ProviderDriverKind.make("codex"),
  turnItem: {
    id: TurnItemId.make("item"),
    threadId: ThreadId.make("thread"),
    runId: null,
    nodeId: null,
    providerThreadId: null,
    providerTurnId: null,
    nativeItemRef: null,
    parentItemId: null,
    ordinal: 0,
    status: streaming ? "running" : "completed",
    title: null,
    startedAt: null,
    completedAt: null,
    updatedAt: DateTime.makeUnsafe("2026-09-14T00:00:00Z"),
    ...(type === "assistant_message" ? { type, messageId: MessageId.make("message") } : { type }),
    text,
    streaming,
  },
});

describe("V2 assistant streaming", () => {
  it("delivers completed paragraphs, coalesces rapid updates, and flushes final text", () => {
    const filter = makeAssistantStreamingFilter("paragraph");
    expect(filter(message("First"), 0)).toBeNull();
    expect(filter(message("First\n\nSec"), 10)).toMatchObject({ message: { text: "First\n\n" } });
    expect(filter(message("First\n\nSecond\n\nThi"), 100)).toBeNull();
    expect(filter(message("First\n\nSecond\n\nThird"), 410)).toMatchObject({
      message: { text: "First\n\nSecond\n\n" },
    });
    const final = message("First\n\nSecond\n\nThird", false);
    expect(filter(final, 420)).toBe(final);
  });
  it("keeps code fences intact", () => {
    expect(splitBufferedAssistantText("Intro\n\n```ts\nx()\n\n")).toEqual({
      ready: "Intro\n\n",
      rest: "```ts\nx()\n\n",
    });
    expect(splitBufferedAssistantText("```ts\nx()\n```\nrest")).toEqual({
      ready: "```ts\nx()\n```\n",
      rest: "rest",
    });
  });
  it("holds streaming text until the full response completes", () => {
    const running = message("partial");
    const final = message("complete", false);
    const buffered = makeAssistantStreamingFilter("turn");
    expect(buffered(running, 0)).toBeNull();
    expect(buffered(message("First\n\nSecond\n\n"), 500)).toBeNull();
    expect(buffered(final, 501)).toBe(final);
  });

  it.each(["assistant_message", "reasoning"] as const)(
    "buffers %s at paragraph boundaries and flushes the final item",
    (type) => {
      const item = (text: string, streaming = true) => turnItem(text, streaming, type);
      const filter = makeAssistantStreamingFilter("paragraph");
      expect(filter(item("First"), 0)).toBeNull();
      expect(filter(item("First\n\nSec"), 10)).toMatchObject({
        turnItem: { text: "First\n\n", streaming: true, type },
      });
      expect(filter(item("First\n\nSecond\n\nThi"), 100)).toBeNull();
      expect(filter(item("First\n\nSecond\n\nThi"), 410)).toMatchObject({
        turnItem: { text: "First\n\nSecond\n\n", streaming: true },
      });
      const final = item("First\n\nSecond\n\nThird", false);
      expect(filter(final, 420)).toBe(final);

      const buffered = makeAssistantStreamingFilter("turn");
      expect(buffered(item("First\n\nSecond"), 0)).toBeNull();
      expect(buffered(final, 1)).toBe(final);
    },
  );

  it.each(["turn", "paragraph"] as const)(
    "suppresses running assistant nodes in %s mode while delivering tool and completed nodes",
    (mode) => {
      const filter = makeAssistantStreamingFilter(mode);
      const running: Extract<ProviderAdapter.ProviderAdapterV2Event, { type: "node.updated" }> = {
        type: "node.updated",
        driver: ProviderDriverKind.make("codex"),
        node: {
          id: NodeId.make("node"),
          threadId: ThreadId.make("thread"),
          runId: null,
          parentNodeId: null,
          rootNodeId: NodeId.make("root"),
          kind: "assistant_message",
          status: "running",
          countsForRun: false,
          providerThreadId: null,
          providerTurnId: null,
          nativeItemRef: null,
          runtimeRequestId: null,
          checkpointScopeId: null,
          startedAt: null,
          completedAt: null,
        },
      };
      expect(filter(running, 0)).toBeNull();
      const completed = { ...running, node: { ...running.node, status: "completed" as const } };
      expect(filter(completed, 1)).toBe(completed);
      const tool = { ...running, node: { ...running.node, kind: "tool_call" as const } };
      expect(filter(tool, 2)).toBe(tool);
    },
  );
});

it("holds a streamed section heading until its content has a boundary", () => {
  expect(splitBufferedAssistantText("Intro\n\n## Results\n\n")).toEqual({
    ready: "Intro\n\n",
    rest: "## Results\n\n",
  });
  expect(splitBufferedAssistantText("**Results**\n\nBody\n\nNext")).toEqual({
    ready: "**Results**\n\nBody\n\n",
    rest: "Next",
  });
});

it.each(["message", "assistant_message", "reasoning"] as const)(
  "matches complete-text boundaries for fragmented %s Markdown",
  (kind) => {
    const filter = makeAssistantStreamingFilter("paragraph");
    const input = (text: string, streaming = true) =>
      kind === "message" ? message(text, streaming) : turnItem(text, streaming, kind);
    const samples = [
      "Intro\n\n## Results\n\nBody\n\nNext",
      "**Results**\n\n- First\n- Second\n\nEnd",
      "- Code\n  ```ts\n  x()\n\n     ```\n  ```\nEnd\n\n",
      "~~~md\n```\n\n### Code heading\n~~~~\nAfter\n\n",
      "A\n1. One\n12. Two\n\n\t\n中文 👩🏽‍💻\r\n\r\nNext",
      "Paragraph\n## Heading\n\n**Subheading**\n\nBody\n\n",
      "```ts\n" + "const value = 1;\n".repeat(500) + "```\nEnd\n\n",
    ];
    // Fixed seeds exercise cuts inside markers, CRLF, indentation and Unicode.
    let seed = 0x51f15e;
    let now = 0;
    for (const text of samples) {
      let previous = "";
      for (let length = 1; length <= text.length;) {
        const prefix = text.slice(0, length);
        const { ready } = splitBufferedAssistantText(prefix);
        const event = input(prefix);
        const expected =
          !ready || ready === previous
            ? null
            : event.type === "message.updated"
              ? { ...event, message: { ...event.message, text: ready } }
              : event.type === "turn_item.updated"
                ? { ...event, turnItem: { ...event.turnItem, text: ready } }
                : event;
        expect(filter(event, now)).toEqual(expected);
        if (expected) previous = ready;
        now += 401;
        seed = (Math.imul(seed, 1_664_525) + 1_013_904_223) >>> 0;
        length += 1 + (seed % 17);
      }
      const final = input(text, false);
      expect(filter(final, now++)).toBe(final);
    }
  },
);

it("rechecks boundaries when a provider replaces or shortens streaming text", () => {
  const filter = makeAssistantStreamingFilter("paragraph");
  const inputs = [
    "Intro\n\n```ts\nconst value = 1;\n",
    "Intro\n\n```ts\nconst value = 1;\n```\nNext\n\n",
    "Changed\n\n~~~ts\nconst value = 1;\n",
    "Changed\n\n~~~ts\nconst value = 1;\n~~~\nNext\n\n",
    "Changed\n\n~~~ts\n",
    "Changed\n\n~~~ts\nconst value = 2;\n~~~\nEnd\n\n",
    "Short",
    "Short\n\nNext",
  ];
  let previous = "";
  for (const [index, text] of inputs.entries()) {
    const { ready } = splitBufferedAssistantText(text);
    const result = filter(message(text), index * 401);
    if (!ready || ready === previous) {
      expect(result).toBeNull();
    } else {
      expect(result).toMatchObject({ message: { text: ready } });
      previous = ready;
    }
  }
});

it("preserves paragraph boundaries when interleaved streams exceed the scan cache", () => {
  const filter = makeAssistantStreamingFilter("paragraph");
  const input = (id: number, text: string) => {
    const event = turnItem(text);
    if (event.type !== "turn_item.updated") throw new Error("Expected a turn item");
    return { ...event, turnItem: { ...event.turnItem, id: TurnItemId.make(`item:${id}`) } };
  };
  for (let id = 0; id < 40; id += 1) {
    expect(filter(input(id, "Intro\n\n```ts\nx()\n"), 0)).toMatchObject({
      turnItem: { text: "Intro\n\n" },
    });
  }
  for (let id = 0; id < 40; id += 1) {
    const text = "Intro\n\n```ts\nx()\n```\nAfter";
    expect(filter(input(id, text), 500)).toMatchObject({
      turnItem: { text: "Intro\n\n```ts\nx()\n```\n" },
    });
  }
});
