import type { ModelSelection, ServerProvider } from "@t3tools/contracts";

/** Options from available providers; the selected legacy model stays visible, other legacy models do not. */
function modelOptions(
  providers: ReadonlyArray<ServerProvider>,
  selected: ModelSelection | undefined,
  includeDriver: (driver: ServerProvider["driver"]) => boolean,
) {
  return providers
    .filter(
      (provider) =>
        includeDriver(provider.driver) &&
        provider.availability !== "unavailable" &&
        provider.status !== "disabled",
    )
    .flatMap((provider) =>
      provider.models
        .filter(
          (model) =>
            !model.isLegacy ||
            (selected?.instanceId === provider.instanceId && selected.model === model.slug),
        )
        .map((model) => ({
          value: JSON.stringify([provider.instanceId, model.slug]),
          label: `${provider.displayName ?? provider.driver} · ${model.name}`,
          selection: { instanceId: provider.instanceId, model: model.slug },
        })),
    );
}

/** Both clients keep the selected legacy model visible without offering other legacy models. */
export function advisorModelOptions(
  providers: ReadonlyArray<ServerProvider>,
  selected?: ModelSelection,
) {
  const options = modelOptions(
    providers,
    selected,
    (driver) => driver === "codex" || driver === "claudeAgent",
  );
  return {
    options,
    fallback:
      options.find((option) => /gpt-6-sol|claude-opus/.test(option.selection.model)) ?? options[0],
  };
}

/**
 * A sidekick runs as an ordinary child thread, so any available provider can be one.
 * The default is a cheap model, since the sidekick exists to take token-heavy work off the lead.
 */
export function sidekickModelOptions(
  providers: ReadonlyArray<ServerProvider>,
  selected?: ModelSelection,
) {
  const options = modelOptions(providers, selected, () => true);
  return {
    options,
    fallback:
      options.find((option) => /haiku|luna|nemotron|mini|flash/i.test(option.selection.model)) ??
      options[0],
  };
}
