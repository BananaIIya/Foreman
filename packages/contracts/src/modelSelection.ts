import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import * as SchemaTransformation from "effect/SchemaTransformation";

import { TrimmedNonEmptyString } from "./baseSchemas.ts";
import { ProviderOptionSelections } from "./model.ts";
import { ProviderInstanceId } from "./providerInstance.ts";

/**
 * Selection of a model on a configured provider instance.
 *
 * `instanceId` is the routing key. The provider driver, credentials,
 * environment, and continuation identity are resolved by the provider
 * instance registry rather than encoded into the selection itself.
 */
const ModelSelectionWire = Schema.Struct({
  instanceId: ProviderInstanceId,
  model: TrimmedNonEmptyString,
  options: Schema.optionalKey(ProviderOptionSelections),
});

const ModelSelectionSource = Schema.Struct({
  provider: Schema.optional(Schema.Unknown),
  instanceId: Schema.optional(Schema.Unknown),
  model: Schema.Unknown,
  options: Schema.optional(Schema.Unknown),
});

/**
 * The decoder still accepts the historical `{ provider, model }` shape while
 * V1 persistence remains readable. Runtime code only receives the canonical
 * instance-based representation.
 */
export const ModelSelection = ModelSelectionSource.pipe(
  Schema.decodeTo(
    ModelSelectionWire,
    SchemaTransformation.transformEffect({
      decode: (raw) => {
        const instanceIdSource =
          raw.instanceId !== undefined
            ? raw.instanceId
            : typeof raw.provider === "string"
              ? raw.provider
              : undefined;
        const base: Record<string, unknown> = {
          instanceId: instanceIdSource,
          model: raw.model,
        };
        if (raw.options !== undefined) base.options = raw.options;
        return Effect.succeed(base as typeof ModelSelectionWire.Encoded);
      },
      encode: (value) => {
        const base: Record<string, unknown> = {
          instanceId: value.instanceId,
          model: value.model,
        };
        if (value.options !== undefined) base.options = value.options;
        return Effect.succeed(base as typeof ModelSelectionSource.Encoded);
      },
    }),
  ),
);
export type ModelSelection = typeof ModelSelection.Type;

/**
 * Legacy worker payloads keep `provider` on disk and on the wire for older clients.
 * Inside the app, workers use the same instance-keyed selection as every other run.
 */
const LegacyWorkerConfiguration = Schema.Struct({
  provider: ModelSelectionWire.fields.instanceId,
  model: ModelSelectionWire.fields.model,
  options: Schema.optional(ProviderOptionSelections),
});
export const WorkerConfiguration = Schema.Union([
  ModelSelectionWire,
  LegacyWorkerConfiguration,
]).pipe(
  Schema.decodeTo(
    Schema.toType(ModelSelectionWire),
    SchemaTransformation.transform({
      decode: (worker) =>
        "instanceId" in worker
          ? worker
          : {
              instanceId: worker.provider,
              model: worker.model,
              ...(worker.options === undefined ? {} : { options: worker.options }),
            },
      encode: (selection) => ({
        provider: selection.instanceId,
        model: selection.model,
        ...(selection.options === undefined ? {} : { options: selection.options }),
      }),
    }),
  ),
);
export type WorkerConfiguration = typeof WorkerConfiguration.Type;
