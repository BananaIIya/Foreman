/**
 * API IDs from Nebius's Nemotron cookbook; catalog reads never perform inference.
 * `temperature` is sent with every request; without one OpenCode sends none and Nebius
 * uses its default.
 */
export const NEBIUS_MODELS: ReadonlyArray<{
  readonly id: string;
  readonly name: string;
  readonly context: number;
  readonly temperature?: number;
}> = [
  // Nano sometimes writes its tool call inside its reasoning, where Nebius drops it. Replaying
  // a real mid-run call, 0.6 gave the right call 19/20 times against 23/30 with no temperature.
  {
    id: "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    name: "Nemotron 3 Nano",
    context: 262144,
    temperature: 0.6,
  },
  { id: "nvidia/nemotron-3-super-120b-a12b", name: "Nemotron 3 Super", context: 262144 },
  { id: "nvidia/Nemotron-3.5-Super-VL", name: "Nemotron 3.5 Super VL", context: 1048576 },
  { id: "nvidia/Nemotron-3-Ultra-550b-a55b", name: "Nemotron 3 Ultra", context: 1048576 },
];

/** Use an environment reference so OpenCode's config and logs never contain the credential. */
export function nebiusOpenCodeConfiguration() {
  return {
    enabled_providers: ["nebius"],
    provider: {
      nebius: {
        npm: "@ai-sdk/openai-compatible",
        name: "Nebius Token Factory",
        options: {
          baseURL: "https://api.tokenfactory.nebius.com/v1",
          apiKey: "{env:NEBIUS_API_KEY}",
        },
        models: Object.fromEntries(
          NEBIUS_MODELS.map((model) => [
            model.id,
            {
              name: model.name,
              tool_call: true,
              limit: { context: model.context, output: 32768 },
              // OpenCode sends a model's options in the request body.
              ...(model.temperature === undefined
                ? {}
                : { options: { temperature: model.temperature } }),
            },
          ]),
        ),
      },
    },
  };
}
