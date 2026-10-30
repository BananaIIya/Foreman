// @effect-diagnostics nodeBuiltinImport:off
import * as NodeFS from "node:fs";
import * as NodeOS from "node:os";
import * as NodePath from "node:path";

import * as NodeServices from "@effect/platform-node/NodeServices";
import { assert, it } from "@effect/vitest";
import { ProviderInstanceId, ProviderSessionId, ThreadId } from "@t3tools/contracts";
import type { OpenCodeSettings } from "../settings.ts";
import * as HostProcess from "@t3tools/shared/HostProcess";
import * as Shell from "@t3tools/shared/shell";
import * as Effect from "effect/Effect";
import * as Fiber from "effect/Fiber";
import * as Layer from "effect/Layer";
import * as TestClock from "effect/testing/TestClock";
import * as HttpClient from "effect/http/HttpClient";
import * as HttpClientResponse from "effect/http/HttpClientResponse";
import * as ChildProcessSpawner from "effect/process/ChildProcessSpawner";

import * as ProviderLatestVersions from "@t3tools/provider-core/server/ProviderLatestVersions";
import * as IdAllocator from "@t3tools/provider-core/server/IdAllocator";
import * as McpProviderSessions from "@t3tools/provider-core/server/McpProviderSessions";
import * as ProviderEventLoggers from "@t3tools/provider-core/server/ProviderEventLoggers";
import * as ProviderMaintenance from "@t3tools/provider-core/server/maintenanceResolver";
import * as OpenCodeRuntime from "./OpenCodeRuntime.ts";
import {
  OPENCODE_2_RESPONSES,
  OPENCODE_2_WORKSPACE_RESPONSES,
  replayOpenCodeServer,
} from "./probeResponses.fixture.ts";
import { OpenCodeDriver, openCodeUpdateFor } from "./driver.ts";
import * as TestProviderHost from "@t3tools/provider-testing/TestProviderHost";

const serverStarts: Array<string> = [];
const reachedServer = (operation: string) =>
  Effect.sync(() => serverStarts.push(operation)).pipe(
    Effect.andThen(
      Effect.fail(
        new OpenCodeRuntime.OpenCodeRuntimeError({
          operation,
          detail: "reached a 1.x server path",
        }),
      ),
    ),
  );
// Reports OpenCode 2 from `--version`; any attempt to reach a server is recorded and refused.
const openCode2Runtime = {
  runOpenCodeCommand: () => Effect.succeed({ stdout: "opencode v2.0.18\n", stderr: "", code: 0 }),
  startOpenCodeServerProcess: () => reachedServer("start"),
  connectToOpenCodeServer: () => reachedServer("connect"),
} as unknown as OpenCodeRuntime.OpenCodeRuntime["Service"];

const layer = Layer.mergeAll(
  IdAllocator.layer,
  McpProviderSessions.layer,
  ProviderLatestVersions.layer,
  TestProviderHost.layer(),
  Layer.succeed(
    ProviderEventLoggers.ProviderEventLoggers,
    ProviderEventLoggers.NoOpProviderEventLoggers,
  ),
  Layer.succeed(OpenCodeRuntime.OpenCodeRuntime, openCode2Runtime),
).pipe(Layer.provideMerge(NodeServices.layer));

const create = (config: Partial<OpenCodeSettings>, http: HttpClient.HttpClient) =>
  OpenCodeDriver.create({
    instanceId: ProviderInstanceId.make("opencode-test"),
    displayName: undefined,
    environment: [],
    enabled: true,
    config: { ...OpenCodeDriver.defaultConfig(), ...config },
  }).pipe(Effect.provideService(HttpClient.HttpClient, http));

const noHttp = HttpClient.make(() => Effect.die("A local binary must not be probed over HTTP"));

it.layer(layer)("OpenCodeDriver runtime selection", (it) => {
  it.effect("never starts a 1.x server for an OpenCode 2 instance", () =>
    Effect.gen(function* () {
      serverStarts.length = 0;
      const instance = yield* create({}, noHttp);
      // Text generation goes to the instance's 2.x server, which this test refuses to start.
      yield* Effect.flip(
        instance.textGeneration.generateThreadTitle({
          cwd: process.cwd(),
          message: "hello",
          modelSelection: { instanceId: instance.instanceId, model: "opencode/big-pickle" },
        }),
      );
      assert.deepStrictEqual(serverStarts, ["start"]);
    }).pipe(Effect.scoped),
  );

  it.effect("lists an OpenCode 2 workspace's own skills and commands from its server", () =>
    Effect.gen(function* () {
      serverStarts.length = 0;
      const requested: Array<string> = [];
      const server = replayOpenCodeServer(
        { ...OPENCODE_2_RESPONSES, ...OPENCODE_2_WORKSPACE_RESPONSES },
        "secret",
        requested,
      );
      const instance = yield* create(
        { serverUrl: "http://127.0.0.1:4096", serverPassword: "secret" },
        server,
      );

      const workspace = yield* instance.snapshotForCwd!("/work");
      assert.includeMembers(
        workspace.skills.map((skill) => skill.name),
        ["plum", "opencode"],
      );
      assert.deepStrictEqual(
        workspace.slashCommands.map((command) => command.name),
        ["compact", "init", "review", "bee"],
      );
      assert.include(requested, "/api/skill");
      assert.deepStrictEqual(serverStarts, []);
    }).pipe(Effect.scoped),
  );

  it.effect("answers capability reads without waiting on an unreachable server", () =>
    Effect.gen(function* () {
      const hang = HttpClient.make(() => Effect.never);
      const instance = yield* create({ serverUrl: "http://127.0.0.1:9" }, hang);

      // No probe has succeeded yet, and the server never answers: the 1.x default applies.
      const capabilities = yield* instance.orchestrationAdapter
        .getCapabilities()
        .pipe(Effect.forkChild);
      yield* Effect.yieldNow;
      assert.isDefined(capabilities.pollUnsafe());
      yield* Fiber.join(capabilities);
    }).pipe(Effect.scoped, Effect.provide(TestClock.layer())),
  );
});

// npm links `<prefix>/bin/opencode` into the package that installed it:
// `opencode-ai` for 1.x and `@opencode/cli` (whose bin is `bin/opencode.exe`) for 2.x.
const npmGlobalInstall = (prefix: string, packagePath: ReadonlyArray<string>, bin: string) => {
  const target = NodePath.join(prefix, "lib", "node_modules", ...packagePath, "bin", bin);
  NodeFS.mkdirSync(NodePath.dirname(target), { recursive: true });
  NodeFS.writeFileSync(target, "#!/bin/sh\n");
  NodeFS.chmodSync(target, 0o755);
  const link = NodePath.join(prefix, "bin", "opencode");
  NodeFS.mkdirSync(NodePath.dirname(link), { recursive: true });
  NodeFS.symlinkSync(target, link);
  return link;
};
const resolveUpdate = (generation: "v1" | "v2", binaryPath: string) =>
  ProviderMaintenance.resolveProviderMaintenanceCapabilitiesEffect(openCodeUpdateFor(generation), {
    binaryPath,
    env: { PATH: "" },
  }).pipe(
    Effect.provideService(
      ChildProcessSpawner.ChildProcessSpawner,
      ChildProcessSpawner.make(() => Effect.die("resolving an update must not spawn")),
    ),
    Effect.provide(NodeServices.layer),
  );

it.effect.skipIf(HostProcess.Platform.defaultValue() === "win32")(
  "updates an npm-global OpenCode 2 install as @opencode/cli and a 1.x one as opencode-ai",
  () =>
    Effect.gen(function* () {
      const root = NodeFS.realpathSync(
        NodeFS.mkdtempSync(NodePath.join(NodeOS.tmpdir(), "t3-opencode-update-")),
      );
      const v2Prefix = NodePath.join(root, "v2");
      const v1Prefix = NodePath.join(root, "v1");
      const v2 = yield* resolveUpdate(
        "v2",
        npmGlobalInstall(v2Prefix, ["@opencode", "cli"], "opencode.exe"),
      );
      assert.deepStrictEqual(v2.update?.args, [
        "install",
        "-g",
        "--prefix",
        v2Prefix,
        "--allow-scripts=@opencode/cli",
        "@opencode/cli@latest",
      ]);
      const v1 = yield* resolveUpdate(
        "v1",
        npmGlobalInstall(v1Prefix, ["opencode-ai"], "opencode"),
      );
      assert.strictEqual(v1.update?.args.at(-1), "opencode-ai@latest");
      // A 1.x install is never offered 2.x's package, or the reverse.
      assert.isNull(
        (yield* resolveUpdate("v2", NodePath.join(v1Prefix, "bin", "opencode"))).update,
      );
      assert.isNull(
        (yield* resolveUpdate("v1", NodePath.join(v2Prefix, "bin", "opencode"))).update,
      );
    }),
);

// The update the driver offers follows the version the binary reports now.
const versionOutput: { current: string | undefined } = { current: undefined };
const versionProbes: Array<string> = [];
const changingRuntime = {
  runOpenCodeCommand: ({ binaryPath }: { readonly binaryPath: string }) =>
    Effect.sync(() => versionProbes.push(binaryPath)).pipe(
      Effect.andThen(
        versionOutput.current === undefined
          ? Effect.fail(
              new OpenCodeRuntime.OpenCodeRuntimeError({
                operation: "version",
                detail: "no version",
              }),
            )
          : Effect.succeed({ stdout: versionOutput.current, stderr: "", code: 0 }),
      ),
    ),
  startOpenCodeServerProcess: () => reachedServer("start"),
  connectToOpenCodeServer: () => reachedServer("connect"),
} as unknown as OpenCodeRuntime.OpenCodeRuntime["Service"];
const layerUpdate = Layer.mergeAll(
  IdAllocator.layer,
  McpProviderSessions.layer,
  ProviderLatestVersions.layer,
  TestProviderHost.layer(),
  Layer.succeed(
    ProviderEventLoggers.ProviderEventLoggers,
    ProviderEventLoggers.NoOpProviderEventLoggers,
  ),
  Layer.succeed(OpenCodeRuntime.OpenCodeRuntime, changingRuntime),
).pipe(Layer.provideMerge(NodeServices.layer));

it.layer(layerUpdate)("OpenCodeDriver updates", (it) => {
  it.effect("never runs the binary for a disabled instance's update check", () =>
    Effect.gen(function* () {
      versionProbes.length = 0;
      versionOutput.current = "opencode v2.0.18\n";
      const instance = yield* OpenCodeDriver.create({
        instanceId: ProviderInstanceId.make("opencode-disabled"),
        displayName: undefined,
        environment: [],
        enabled: false,
        config: { ...OpenCodeDriver.defaultConfig(), binaryPath: "opencode" },
      }).pipe(Effect.provideService(HttpClient.HttpClient, noHttp));
      const maintenance = yield* instance.snapshot.resolveMaintenance();
      yield* instance.snapshot.resolveMaintenance({ fresh: true });
      assert.isNull(maintenance.update);
      assert.deepStrictEqual(versionProbes, []);
    }).pipe(Effect.scoped),
  );

  it.effect.skipIf(HostProcess.Platform.defaultValue() === "win32")(
    "offers no package update for an unknown version and follows a changed one on a fresh read",
    () =>
      Effect.gen(function* () {
        const root = NodeFS.realpathSync(
          NodeFS.mkdtempSync(NodePath.join(NodeOS.tmpdir(), "t3-opencode-driver-update-")),
        );
        const binaryPath = npmGlobalInstall(NodePath.join(root, "v1"), ["opencode-ai"], "opencode");
        const instance = yield* OpenCodeDriver.create({
          instanceId: ProviderInstanceId.make("opencode-update"),
          displayName: undefined,
          environment: [],
          enabled: true,
          config: { ...OpenCodeDriver.defaultConfig(), binaryPath },
        }).pipe(Effect.provideService(HttpClient.HttpClient, noHttp));

        // The version could not be read: which package owns the binary is unknown.
        versionOutput.current = undefined;
        const unknown = yield* instance.snapshot.resolveMaintenance({ fresh: true });
        assert.isNull(unknown.update);
        assert.isNull(unknown.packageName);

        versionOutput.current = "1.18.32\n";
        const v1 = yield* instance.snapshot.resolveMaintenance({ fresh: true });
        assert.strictEqual(v1.packageName, "opencode-ai");

        // The same path now reports 2.x (reinstalled in place): a fresh read re-probes
        // and never offers 1.x's package for it.
        versionOutput.current = "opencode v2.0.18\n";
        const v2 = yield* instance.snapshot.resolveMaintenance({ fresh: true });
        assert.strictEqual(v2.packageName, "@opencode/cli");
        assert.isNull(v2.update);
      }).pipe(Effect.scoped),
  );
});

it.layer(layer)("Nebius opt-in gate", (it) => {
  it.effect.each([false, true])(
    "refuses inference without an enabled instance and credential (enabled=%s)",
    (enabled) =>
      Effect.gen(function* () {
        const { NebiusDriver } = yield* Effect.promise(() => import("./nebiusDriver.ts"));
        serverStarts.length = 0;
        const instance = yield* NebiusDriver.create({
          instanceId: ProviderInstanceId.make("nebius"),
          displayName: undefined,
          environment: [],
          enabled,
          config: {
            ...NebiusDriver.defaultConfig(),
            apiKey: enabled ? "" : "test-disabled-credential",
          },
        }).pipe(Effect.provideService(HttpClient.HttpClient, noHttp));
        const error = yield* Effect.flip(
          instance.orchestrationAdapter.openSession({
            threadId: ThreadId.make("nebius-gate"),
            providerSessionId: ProviderSessionId.make("nebius-gate-session"),
            modelSelection: {
              instanceId: instance.instanceId,
              model: "nebius/nvidia/nemotron-3-super-120b-a12b",
            },
            runtimePolicy: { runtimeMode: "full-access", interactionMode: "default", cwd: "/work" },
          }),
        );
        assert.equal(error._tag, "ProviderAdapterOpenSessionError");
        assert.deepStrictEqual(serverStarts, []);
      }).pipe(Effect.scoped),
  );

  it.effect("disabled Nebius reads its catalog but never reaches a process or HTTP server", () =>
    Effect.gen(function* () {
      const { NebiusDriver } = yield* Effect.promise(() => import("./nebiusDriver.ts"));
      serverStarts.length = 0;
      const instance = yield* NebiusDriver.create({
        instanceId: ProviderInstanceId.make("nebius"),
        displayName: undefined,
        environment: [],
        enabled: false,
        config: { ...NebiusDriver.defaultConfig(), apiKey: "test-disabled-credential" },
      }).pipe(Effect.provideService(HttpClient.HttpClient, noHttp));
      const snapshot = yield* instance.snapshot.refresh;
      assert.equal(snapshot.driver, "nebius");
      assert.equal(snapshot.enabled, false);
      assert.equal(snapshot.models.length, 3);
      assert.isTrue(snapshot.models.every((model) => !model.isCustom));
      yield* instance.snapshotForCwd!("/work");
      yield* Effect.flip(
        instance.textGeneration.generateThreadTitle({
          cwd: "/work",
          message: "hello",
          modelSelection: {
            instanceId: instance.instanceId,
            model: snapshot.models[0]!.slug,
          },
        }),
      );
      assert.deepStrictEqual(serverStarts, []);
    }).pipe(Effect.scoped),
  );
  it.effect("enabled Nebius lists the three worker models with its own driver identity", () =>
    Effect.gen(function* () {
      const { NebiusDriver, normalizeNebiusSnapshot } = yield* Effect.promise(
        () => import("./nebiusDriver.ts"),
      );
      const instance = yield* NebiusDriver.create({
        instanceId: ProviderInstanceId.make("nebius"),
        displayName: undefined,
        environment: [],
        enabled: true,
        config: { ...NebiusDriver.defaultConfig(), enabled: true, apiKey: "test-credential" },
      }).pipe(Effect.provideService(HttpClient.HttpClient, noHttp));
      const snapshot = yield* instance.snapshot.getSnapshot;
      assert.equal(instance.driverKind, "nebius");
      assert.equal(instance.orchestrationAdapter.driver, "nebius");
      assert.isTrue(snapshot.models.every((model) => !model.isCustom));
      assert.deepStrictEqual(
        snapshot.models.map((model) => model.name),
        ["Nemotron 3 Nano", "Nemotron 3 Super", "Nemotron 3 Ultra"],
      );
      const catalog = normalizeNebiusSnapshot(
        {
          ...snapshot,
          installed: true,
          models: [
            ...snapshot.models,
            { ...snapshot.models[0]!, slug: "nebius/other-model", name: "Other model" },
          ],
          auth: { status: "authenticated" as const },
          status: "ready" as const,
        },
        false,
      );
      assert.equal(catalog.models.length, 3);
      assert.equal(catalog.auth.status, "unauthenticated");
      assert.equal(catalog.status, "warning");
      assert.include(catalog.message!, "Nebius API key");
    }).pipe(Effect.scoped),
  );
});

// Records every attempt to run OpenCode, with the environment it would get, and every HTTP request.
const runtimeCalls: Array<{ readonly operation: string; readonly env: NodeJS.ProcessEnv }> = [];
const recordRuntime = (operation: string, env: NodeJS.ProcessEnv | undefined) =>
  Effect.sync(() => runtimeCalls.push({ operation, env: env ?? {} }));
const recordingRuntime = {
  runOpenCodeCommand: ({ environment }: { readonly environment?: NodeJS.ProcessEnv }) =>
    recordRuntime("command", environment).pipe(
      Effect.as({ stdout: "opencode v2.0.18\n", stderr: "", code: 0 }),
    ),
  startOpenCodeServerProcess: ({ environment }: { readonly environment?: NodeJS.ProcessEnv }) =>
    recordRuntime("start", environment).pipe(Effect.andThen(reachedServer("start"))),
  connectToOpenCodeServer: ({ environment }: { readonly environment?: NodeJS.ProcessEnv }) =>
    recordRuntime("connect", environment).pipe(Effect.andThen(reachedServer("connect"))),
} as unknown as OpenCodeRuntime.OpenCodeRuntime["Service"];
const requests: Array<string> = [];
const recordingHttp = HttpClient.make((request) =>
  Effect.sync(() => {
    requests.push(request.url);
    return HttpClientResponse.fromWeb(request, new Response(null, { status: 403 }));
  }),
);
const layerRecording = Layer.mergeAll(
  IdAllocator.layer,
  McpProviderSessions.layer,
  ProviderLatestVersions.layer,
  TestProviderHost.layer(),
  Layer.succeed(
    ProviderEventLoggers.ProviderEventLoggers,
    ProviderEventLoggers.NoOpProviderEventLoggers,
  ),
  Layer.succeed(OpenCodeRuntime.OpenCodeRuntime, recordingRuntime),
).pipe(Layer.provideMerge(NodeServices.layer));

// An OpenCode Go key the ordinary OpenCode instance would use to read its account's limits.
const goAuth = [
  {
    name: "OPENCODE_AUTH_CONTENT",
    value: JSON.stringify({ "opencode-go": { type: "api", key: "test-go-key" } }),
    sensitive: false,
  },
];
const NEBIUS_ONLY_VARIABLES = [
  "NEBIUS_API_KEY",
  "OPENCODE_CONFIG_CONTENT",
  "OPENCODE_DISABLE_MODELS_FETCH",
  "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
] as const;

it.layer(layerRecording)("Nebius isolation", (it) => {
  it.effect("enabled Nebius without a key never runs OpenCode or calls the network", () =>
    Effect.gen(function* () {
      const { NebiusDriver } = yield* Effect.promise(() => import("./nebiusDriver.ts"));
      runtimeCalls.length = 0;
      requests.length = 0;
      const lookups: Array<string> = [];
      const instance = yield* NebiusDriver.create({
        instanceId: ProviderInstanceId.make("nebius"),
        displayName: undefined,
        environment: goAuth,
        enabled: true,
        config: { ...NebiusDriver.defaultConfig(), enabled: true, apiKey: "" },
      }).pipe(
        Effect.provideService(HttpClient.HttpClient, recordingHttp),
        Effect.provideService(Shell.CommandAvailability, (command) =>
          Effect.sync(() => lookups.push(command)).pipe(Effect.as(true)),
        ),
      );
      const snapshot = yield* instance.snapshot.refresh;
      yield* instance.snapshotForCwd!("/work");
      yield* instance.snapshot.resolveMaintenance({ fresh: true });
      assert.isTrue(instance.enabled);
      assert.isTrue(snapshot.enabled);
      assert.isTrue(snapshot.installed);
      assert.equal(snapshot.auth.status, "unauthenticated");
      assert.equal(snapshot.status, "warning");
      assert.include(snapshot.message!, "Nebius API key");
      assert.equal(snapshot.models.length, 3);
      assert.deepStrictEqual(lookups, ["opencode"]);
      assert.deepStrictEqual(runtimeCalls, []);
      assert.deepStrictEqual(requests, []);
    }).pipe(Effect.scoped),
  );

  it.effect(
    "keeps Nebius env, Nemotron temperature, and OpenCode Go limits on their own instance",
    () =>
      Effect.gen(function* () {
        const { NebiusDriver } = yield* Effect.promise(() => import("./nebiusDriver.ts"));
        const refreshed = <A, E, R>(
          create: Effect.Effect<
            { readonly snapshot: { readonly refresh: Effect.Effect<A, E> } },
            E,
            R
          >,
        ) =>
          Effect.gen(function* () {
            runtimeCalls.length = 0;
            requests.length = 0;
            const snapshot = yield* (yield* create).snapshot.refresh;
            return { snapshot, calls: [...runtimeCalls], requests: [...requests] };
          });
        const openCode = yield* refreshed(
          OpenCodeDriver.create({
            instanceId: ProviderInstanceId.make("opencode"),
            displayName: undefined,
            environment: goAuth,
            enabled: true,
            config: { ...OpenCodeDriver.defaultConfig(), enabled: true },
          }).pipe(Effect.provideService(HttpClient.HttpClient, recordingHttp)),
        );
        const nebius = yield* refreshed(
          NebiusDriver.create({
            instanceId: ProviderInstanceId.make("nebius"),
            displayName: undefined,
            environment: goAuth,
            enabled: true,
            config: { ...NebiusDriver.defaultConfig(), enabled: true, apiKey: "test-credential" },
          }).pipe(Effect.provideService(HttpClient.HttpClient, recordingHttp)),
        );

        // Both instances ran OpenCode: Nebius with its own config, OpenCode with the host's.
        assert.isNotEmpty(openCode.calls);
        assert.isNotEmpty(nebius.calls);
        for (const { env } of nebius.calls) {
          assert.equal(env.NEBIUS_API_KEY, "test-credential");
          assert.equal(env.OPENCODE_DISABLE_CLAUDE_CODE_SKILLS, "true");
          assert.include(env.OPENCODE_CONFIG_CONTENT!, '"temperature":0.6');
        }
        for (const { env } of openCode.calls) {
          for (const name of NEBIUS_ONLY_VARIABLES) assert.equal(env[name], process.env[name]);
          assert.notInclude(env.OPENCODE_CONFIG_CONTENT ?? "", "tokenfactory.nebius.com");
        }

        // Only the OpenCode instance reads the host's OpenCode Go account.
        const goUsage = "https://opencode.ai/zen/go/v1/usage";
        assert.include(openCode.requests, goUsage);
        assert.notInclude(nebius.requests, goUsage);
        assert.deepStrictEqual(nebius.snapshot.usageLimits?.windows ?? [], []);
      }).pipe(Effect.scoped),
  );
});
