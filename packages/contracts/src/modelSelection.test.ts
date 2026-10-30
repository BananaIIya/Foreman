import { assert, it } from "@effect/vitest";
import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";

import { ModelSelection, WorkerConfiguration } from "./modelSelection.ts";
import { ProviderInstanceId } from "./providerInstance.ts";

// ── ModelSelection: instance-keyed wire shape + legacy decoder ────────
//
// `ModelSelection` is routing-keyed on `instanceId` — never a driver kind.
// Persisted and in-flight payloads from pre-instance builds carry a
// `provider` field whose value was a driver kind; those payloads are migrated
// at the wire boundary by
// promoting `provider` to the default instance id for that driver
// (built-in drivers use the driver kind slug as their default instance id, so
// the migration is a 1:1 rename).
//
// These tests pin the rollback/fork tolerance invariant: legacy payloads
// decode cleanly for fork-provided drivers, and the decoded form uses
// `instanceId` uniformly regardless of origin.

const decodeModelSelection = Schema.decodeUnknownEffect(ModelSelection);
const encodeModelSelection = Schema.encodeUnknownEffect(ModelSelection);
const decodeWorker = Schema.decodeUnknownSync(WorkerConfiguration);
const encodeWorker = Schema.encodeSync(WorkerConfiguration);

it("preserves effort and speed selections through persisted worker configuration", () => {
  const selection = {
    instanceId: ProviderInstanceId.make("claudeAgent"),
    model: "claude-haiku-5-5",
    options: [
      { id: "effort", value: "medium" },
      { id: "fastMode", value: false },
    ],
  };
  const legacy = {
    provider: selection.instanceId,
    model: selection.model,
    options: selection.options,
  };
  const worker = decodeWorker(legacy);
  assert.deepEqual(worker, selection);
  assert.deepEqual(decodeWorker(worker), selection);
  assert.deepEqual(encodeWorker(worker), legacy);
  const withoutOptions = { provider: selection.instanceId, model: selection.model };
  assert.deepEqual(decodeWorker(withoutOptions), {
    instanceId: selection.instanceId,
    model: selection.model,
  });
  assert.deepEqual(encodeWorker(decodeWorker(withoutOptions)), withoutOptions);
});

it("reads legacy worker options and custom provider instances without changing the wire key", () => {
  const worker = decodeWorker({
    provider: "claude_personal",
    model: "claude-haiku-5-5",
    options: { effort: "medium", fastMode: false, obsolete: null },
  });
  assert.deepStrictEqual(worker, {
    instanceId: ProviderInstanceId.make("claude_personal"),
    model: "claude-haiku-5-5",
    options: [
      { id: "effort", value: "medium" },
      { id: "fastMode", value: false },
    ],
  });
  assert.deepStrictEqual(encodeWorker(worker), {
    provider: "claude_personal",
    model: worker.model,
    options: worker.options,
  });
});

it.effect("ModelSelection migrates legacy `provider` field to `instanceId`", () =>
  Effect.gen(function* () {
    const parsed = yield* decodeModelSelection({
      provider: "codex",
      model: "gpt-5-codex",
      options: [{ id: "reasoningEffort", value: "high" }],
    });
    assert.strictEqual(parsed.instanceId, ProviderInstanceId.make("codex"));
    assert.strictEqual(parsed.model, "gpt-5-codex");
    assert.deepStrictEqual(parsed.options, [{ id: "reasoningEffort", value: "high" }]);
  }),
);

it.effect("ModelSelection accepts an explicit instanceId routing key", () =>
  Effect.gen(function* () {
    const parsed = yield* decodeModelSelection({
      instanceId: "codex_personal",
      model: "gpt-5-codex",
    });
    assert.strictEqual(parsed.instanceId, ProviderInstanceId.make("codex_personal"));
  }),
);

it.effect("ModelSelection prefers explicit instanceId over legacy provider", () =>
  Effect.gen(function* () {
    const parsed = yield* decodeModelSelection({
      provider: "codex",
      instanceId: "codex_personal",
      model: "gpt-5-codex",
    });
    assert.strictEqual(parsed.instanceId, ProviderInstanceId.make("codex_personal"));
  }),
);

it.effect(
  "ModelSelection decodes unknown driver kinds via legacy provider (rollback / fork invariant)",
  () =>
    Effect.gen(function* () {
      const parsed = yield* decodeModelSelection({
        provider: "ollama",
        model: "llama3:70b",
        options: [{ id: "temperature", value: "0.4" }],
      });
      assert.strictEqual(parsed.instanceId, ProviderInstanceId.make("ollama"));
      assert.strictEqual(parsed.model, "llama3:70b");
    }),
);

it.effect("ModelSelection encodes to the canonical instanceId wire form", () =>
  Effect.gen(function* () {
    const decoded = yield* decodeModelSelection({
      provider: "ollama",
      model: "llama3:70b",
      options: [{ id: "temperature", value: "0.4" }],
    });
    const encoded = yield* encodeModelSelection(decoded);
    assert.deepStrictEqual(encoded, {
      instanceId: "ollama",
      model: "llama3:70b",
      options: [{ id: "temperature", value: "0.4" }],
    });
  }),
);

it.effect("ModelSelection rejects malformed instance ids", () =>
  Effect.gen(function* () {
    const result = yield* Effect.exit(
      decodeModelSelection({
        instanceId: "1invalid", // must start with a letter
        model: "x",
      }),
    );
    assert.strictEqual(result._tag, "Failure");
  }),
);

it.effect(
  "normalizes legacy object-shaped options on decode and re-encodes as a canonical array",
  () =>
    Effect.gen(function* () {
      const decoded = yield* decodeModelSelection({
        provider: "claudeAgent",
        model: "claude-opus-4-6",
        options: {
          effort: "max",
          fastMode: true,
          // Falsy/garbage entries are dropped, matching migration 026.
          emptyStr: "   ",
          nullish: null,
          nested: { foo: 1 },
        },
      });
      assert.deepStrictEqual(decoded.options, [
        { id: "effort", value: "max" },
        { id: "fastMode", value: true },
      ]);
      const encoded = yield* encodeModelSelection(decoded);
      assert.deepStrictEqual(encoded.options, decoded.options);
    }),
);
