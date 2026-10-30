import { makeProviderSettingsSchema, secretSetting, TrimmedString } from "@t3tools/contracts";
import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import { OpenCodeSettings } from "./openCodeSettings.ts";

export const NebiusSettings = makeProviderSettingsSchema(
  {
    enabled: OpenCodeSettings.fields.enabled,
    binaryPath: OpenCodeSettings.fields.binaryPath,
    apiKey: TrimmedString.pipe(
      Schema.withDecodingDefault(Effect.succeed("")),
      secretSetting,
      Schema.annotateKey({
        title: "Nebius API key",
        description: "Stored in this server's secrets. Required to run Nebius models.",
        providerSettingsForm: {
          control: "password",
          placeholder: "Enter your Nebius API key",
          clearWhenEmpty: "omit",
        },
      }),
    ),
  },
  { order: ["binaryPath", "apiKey"] },
);
export type NebiusSettings = typeof NebiusSettings.Type;
