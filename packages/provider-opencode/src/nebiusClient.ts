import { ProviderDriverKind } from "@t3tools/contracts";
import { defineProviderClient } from "@t3tools/provider-core/client";
import { NebiusSettings } from "./nebiusSettings.ts";

export const nebiusClient = defineProviderClient({
  driverKind: ProviderDriverKind.make("nebius"),
  label: "Nebius · OpenCode",
  settingsSchema: NebiusSettings,
});
