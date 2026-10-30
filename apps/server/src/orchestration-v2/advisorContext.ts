import type { AdvisorPipelineState } from "@t3tools/contracts";
import * as Effect from "effect/Effect";
import * as FileSystem from "effect/FileSystem";
import * as Path from "effect/Path";
import { boundedAdvisorText } from "./advisorPlan.ts";

// Advice has no workspace tools. Supply bounded evidence without copying chat
// history or recursively loading the repository into the expensive model.
export const advisorContext = Effect.fn("advisorContext")(function* (
  cwd: string | null,
  state: AdvisorPipelineState,
) {
  if (!cwd) return "No workspace excerpts available.";
  const fs = yield* FileSystem.FileSystem;
  const path = yield* Path.Path;
  const root = yield* fs.realPath(cwd);
  const names = yield* fs.readDirectory(root);
  const mentioned =
    `${state.task ?? ""}\n${state.summary}\n${state.diff ?? ""}`.match(
      /[\w@-]+(?:\/[\w.@-]+)*\.[\w]+/g,
    ) ?? [];
  const candidates = [
    ...new Set([
      "AGENTS.md",
      "package.json",
      ...(state.plan?.files ?? []),
      ...mentioned,
      "README.md",
      ...names.sort().filter((name) => /\.[cm]?[jt]sx?$/.test(name)),
    ]),
  ]
    .filter(
      (file) =>
        !file.split("/").some((part) => part.startsWith(".") || part === "node_modules") &&
        /\.(?:[cm]?[jt]sx?|json|md|py|rs|go|java|rb|css|html|yaml|yml|toml)$/.test(file),
    )
    .slice(0, 8);
  const chunks = [
    `Workspace root entries (partial):\n${boundedAdvisorText(names.sort().join("\n"), 2_000)}`,
  ];
  for (const file of candidates) {
    const excerpt = yield* Effect.gen(function* () {
      const resolved = yield* fs.realPath(path.resolve(root, file));
      if (!resolved.startsWith(root + path.sep)) return "";
      const stat = yield* fs.stat(resolved);
      if (stat.type !== "File" || stat.size > 128_000n) return "";
      const contents = yield* fs.readFileString(resolved);
      if (contents.includes("\0")) return "";
      return `\nFile: ${file}\n${boundedAdvisorText(contents, 2_000)}`;
    }).pipe(Effect.orElseSucceed(() => ""));
    if (excerpt) chunks.push(excerpt);
  }
  return boundedAdvisorText(chunks.join("\n"), 12_000);
});
