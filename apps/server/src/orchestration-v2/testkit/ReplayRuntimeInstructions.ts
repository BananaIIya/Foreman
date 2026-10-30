import type { ProviderDriverKind, ProviderReplayTranscript } from "@t3tools/contracts";

import { buildRuntimeInstructions } from "@t3tools/provider-core/server/runtimeInstructions";
import { promptWithStableInstructions } from "@t3tools/provider-core/server/promptCachePolicy";

// Recordings made before the invariant pull-request instructions moved ahead
// of the per-model runtime info (prompt cache prefix).
const LEGACY_RUNTIME_INSTRUCTIONS =
  /^(<runtime_info>[\s\S]*?<\/runtime_info>)\n\n(<pull_request_linking>[\s\S]*<\/pull_request_linking>)$/;

/** Muse records the instructions as their own `turn/start` input part. */
function materializeMuseRuntimeInstructionOrder(
  transcript: ProviderReplayTranscript,
): ProviderReplayTranscript {
  return {
    ...transcript,
    entries: transcript.entries.map((entry) => {
      const frame = entry.type === "expect_outbound" ? entry.frame : undefined;
      if (
        typeof frame !== "object" ||
        frame === null ||
        !("method" in frame) ||
        frame.method !== "turn/start" ||
        !("params" in frame) ||
        typeof frame.params !== "object" ||
        frame.params === null ||
        !("input" in frame.params) ||
        !Array.isArray(frame.params.input)
      )
        return entry;
      const input: ReadonlyArray<unknown> = frame.params.input;
      return {
        ...entry,
        frame: {
          ...frame,
          params: {
            ...frame.params,
            input: input.map((part) =>
              typeof part === "object" &&
              part !== null &&
              "text" in part &&
              typeof part.text === "string"
                ? { ...part, text: part.text.replace(LEGACY_RUNTIME_INSTRUCTIONS, "$2\n\n$1") }
                : part,
            ),
          },
        },
      };
    }),
  };
}

/** Adds current runtime context to legacy prompt expectations, keeping outbound matching exact. */
export function materializeReplayTranscriptRuntimeInstructions(
  transcript: ProviderReplayTranscript,
  runtime: { readonly driver: ProviderDriverKind; readonly model: string },
): ProviderReplayTranscript {
  if (runtime.driver === "muse") return materializeMuseRuntimeInstructionOrder(transcript);
  const harness =
    runtime.driver === "cursor"
      ? "Cursor"
      : runtime.driver === "grok"
        ? "Grok"
        : runtime.driver === "acpRegistry"
          ? "acpRegistry"
          : undefined;
  if (harness === undefined) return transcript;
  const instructions = buildRuntimeInstructions({ harness, model: runtime.model });

  return {
    ...transcript,
    entries: transcript.entries.map((entry) => {
      if (entry.type !== "expect_outbound") return entry;
      const frame = entry.frame;
      if (typeof frame !== "object" || frame === null) return entry;
      if (
        runtime.driver === "cursor" &&
        "type" in frame &&
        frame.type === "run.start" &&
        "message" in frame &&
        typeof frame.message === "string"
      ) {
        return frame.message.startsWith(instructions) || frame.message.endsWith(instructions)
          ? entry
          : {
              ...entry,
              frame: {
                ...frame,
                message: promptWithStableInstructions(frame.message, instructions),
              },
            };
      }
      if (
        "method" in frame &&
        frame.method === "session/prompt" &&
        "params" in frame &&
        typeof frame.params === "object" &&
        frame.params !== null &&
        "prompt" in frame.params &&
        Array.isArray(frame.params.prompt)
      ) {
        const prompt: ReadonlyArray<unknown> = frame.params.prompt;
        const firstPart = prompt[0];
        const commandFirst =
          typeof firstPart === "object" &&
          firstPart !== null &&
          "text" in firstPart &&
          typeof firstPart.text === "string" &&
          firstPart.text.trimStart().startsWith("/");
        const instructionPart = commandFirst ? prompt.at(-1) : firstPart;
        if (
          typeof instructionPart === "object" &&
          instructionPart !== null &&
          "type" in instructionPart &&
          instructionPart.type === "text" &&
          "text" in instructionPart &&
          instructionPart.text === instructions
        ) {
          return entry;
        }
        return {
          ...entry,
          frame: {
            ...frame,
            params: {
              ...frame.params,
              prompt: commandFirst
                ? [...prompt, { type: "text", text: instructions }]
                : [{ type: "text", text: instructions }, ...prompt],
            },
          },
        };
      }
      return entry;
    }),
  };
}
