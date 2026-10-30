import { ProviderDriverKind, TextGenerationError, type ServerProvider } from "@t3tools/contracts";
import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import * as Stream from "effect/Stream";
import * as Shell from "@t3tools/shared/shell";
import { ProviderAdapterOpenSessionError } from "@t3tools/provider-core/server/ProviderAdapter";
import type { ProviderDriver } from "@t3tools/provider-core/server/driver";
import { mergeProviderInstanceEnvironment } from "@t3tools/provider-core/server/instanceEnvironment";
import type { ProviderTextGeneration } from "@t3tools/provider-core/server/textGeneration";
import { NebiusSettings } from "../nebiusSettings.ts";
import { NEBIUS_MODELS, nebiusOpenCodeConfiguration } from "../nebiusModels.ts";
import { makeOpenCodeDriver, type OpenCodeDriverEnv } from "./driver.ts";
import { nebiusUsageReader, type OpenCodeUsageReaderEnv } from "./usage.ts";

const driverKind = ProviderDriverKind.make("nebius");
const decodeNebiusSettings = Schema.decodeSync(NebiusSettings);
const openCode = makeOpenCodeDriver(driverKind);
const openCodeConfiguration = Schema.encodeSync(Schema.fromJsonString(Schema.Unknown))(
  nebiusOpenCodeConfiguration(),
);
const builtInModelSlugs = new Set<string>(NEBIUS_MODELS.map((model) => `nebius/${model.id}`));
// OpenCode accepts these as custom models; T3 must publish them as this driver's built-in catalog.
export const normalizeNebiusSnapshot = <T extends ServerProvider>(
  snapshot: T,
  hasApiKey: boolean,
): T => ({
  ...snapshot,
  models: snapshot.models
    .filter((model) => builtInModelSlugs.has(model.slug))
    .map((model) => ({ ...model, isCustom: false })),
  ...(snapshot.enabled && !hasApiKey ? missingApiKey(snapshot.installed) : {}),
});

const missingApiKey = (installed: boolean) => ({
  enabled: true,
  installed,
  auth: { status: "unauthenticated" as const },
  status: "warning" as const,
  message: "Configure a Nebius API key on this server to run Nemotron models.",
});

const disabledGeneration = Effect.fail(
  new TextGenerationError({
    operation: "nebius",
    detail: "Enable Nebius and configure its API key before running inference.",
  }),
);
const unavailableTextGeneration: ProviderTextGeneration = {
  generateCommitMessage: () => disabledGeneration,
  generatePrContent: () => disabledGeneration,
  generateBranchName: () => disabledGeneration,
  generateThreadTitle: () => disabledGeneration,
  generateReviewGuide: () => disabledGeneration,
};

export const NebiusDriver: ProviderDriver<
  NebiusSettings,
  OpenCodeDriverEnv,
  OpenCodeUsageReaderEnv
> = {
  driverKind,
  metadata: { displayName: "Nebius · OpenCode", supportsMultipleInstances: true },
  configSchema: NebiusSettings,
  defaultConfig: () => decodeNebiusSettings({}),
  usage: nebiusUsageReader,
  create: (input) =>
    Effect.gen(function* () {
      const { apiKey, ...settings } = input.config;
      const hasApiKey = apiKey.length > 0;
      const canRun = input.enabled && hasApiKey;
      // Only a runnable instance receives a credential, a custom OpenCode config, or a
      // running OpenCode: without a key, Nebius never probes, spawns, or calls the network.
      const environment = canRun
        ? [
            ...input.environment.filter(
              (variable) =>
                ![
                  "NEBIUS_API_KEY",
                  "OPENCODE_CONFIG_CONTENT",
                  "OPENCODE_DISABLE_MODELS_FETCH",
                  "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
                ].includes(variable.name),
            ),
            { name: "NEBIUS_API_KEY", value: apiKey, sensitive: true },
            {
              name: "OPENCODE_CONFIG_CONTENT",
              value: openCodeConfiguration,
              sensitive: false,
            },
            { name: "OPENCODE_DISABLE_MODELS_FETCH", value: "true", sensitive: false },
            // Nebius has no prompt-cache discount, so every call pays for the whole prompt.
            // OpenCode lists the user's ~/.claude skills in each one (~3k tokens).
            { name: "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", value: "true", sensitive: false },
          ]
        : [];
      const instance = yield* openCode.create({
        ...input,
        displayName: input.displayName ?? "Nebius · OpenCode",
        environment,
        enabled: canRun,
        config: {
          ...openCode.defaultConfig(),
          ...settings,
          enabled: canRun,
          customModels: NEBIUS_MODELS.map((model) => ({
            slug: `nebius/${model.id}`,
            name: model.name,
            capabilities: { optionDescriptors: [] },
          })),
        },
      });
      // Enabled without a key: report it as enabled but unauthenticated. Finding the binary
      // on PATH reads the filesystem only; it never runs OpenCode.
      const keyless =
        input.enabled && !hasApiKey
          ? missingApiKey(
              yield* (yield* Shell.CommandAvailability)(settings.binaryPath, {
                env: yield* mergeProviderInstanceEnvironment(input.environment),
              }),
            )
          : undefined;
      const projectSnapshot = (snapshot: ServerProvider) =>
        normalizeNebiusSnapshot(keyless ? { ...snapshot, ...keyless } : snapshot, hasApiKey);
      return {
        ...instance,
        enabled: input.enabled,
        snapshot: {
          ...instance.snapshot,
          getSnapshot: instance.snapshot.getSnapshot.pipe(Effect.map(projectSnapshot)),
          refresh: instance.snapshot.refresh.pipe(Effect.map(projectSnapshot)),
          streamChanges: instance.snapshot.streamChanges.pipe(Stream.map(projectSnapshot)),
        },
        ...(instance.snapshotForCwd
          ? {
              snapshotForCwd: (cwd: string) =>
                instance.snapshotForCwd!(cwd).pipe(Effect.map(projectSnapshot)),
            }
          : {}),
        // The registry also rejects disabled instances; enforce the gate at the adapter boundary.
        orchestrationAdapter: {
          ...instance.orchestrationAdapter,
          openSession: (request) =>
            canRun
              ? instance.orchestrationAdapter.openSession(request)
              : Effect.fail(
                  new ProviderAdapterOpenSessionError({
                    driver: driverKind,
                    providerSessionId: request.providerSessionId,
                  }),
                ),
        },
        textGeneration: canRun ? instance.textGeneration : unavailableTextGeneration,
      };
    }),
};
