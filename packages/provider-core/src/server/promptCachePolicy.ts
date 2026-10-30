import * as Predicate from "effect/Predicate";

/**
 * Keep T3-owned instructions before changing user content for native prefix
 * caching. Slash commands must still reach the harness at the start of input.
 * Cache storage, retention and model isolation remain owned by that harness.
 */
export function promptWithStableInstructions(prompt: string, instructions: string): string {
  return prompt.trimStart().startsWith("/")
    ? `${prompt}\n\n${instructions}`
    : `${instructions}\n\n${prompt}`;
}

/**
 * Equivalent object ordering must not restart a live provider query. Arrays
 * retain their meaning (for example command arguments); callers normalize
 * set-valued fields explicitly. This is a local identity, never a cache key
 * sent to a provider or shared across provider/model selections.
 */
export function stableProviderConfigurationKey(value: Readonly<Record<string, unknown>>): string {
  return JSON.stringify(value, (_key, part: unknown) =>
    Predicate.isObject(part) && !Array.isArray(part)
      ? Object.fromEntries(
          Object.keys(part)
            .sort()
            .map((key) => [key, part[key]]),
        )
      : part,
  );
}
