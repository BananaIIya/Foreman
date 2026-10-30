import * as NodeFSP from "node:fs/promises";
import * as NodePath from "node:path";
import * as NodeURL from "node:url";
import * as NodeChildProcess from "node:child_process";
import * as NodeFS from "node:fs";
import * as NodeCrypto from "node:crypto";

export const hash = (value) => NodeCrypto.createHash("sha256").update(value).digest("hex");

export async function writeFixture(task, workspace, solution = task.publicFiles["solution.mjs"]) {
  await NodeFSP.mkdir(workspace, { recursive: true });
  for (const [name, content] of Object.entries(task.publicFiles))
    await NodeFSP.writeFile(
      NodePath.join(workspace, name),
      name === "solution.mjs" ? solution : content,
    );
}

// Evaluate in a separate process: hidden assertions never enter the model's workspace or RPC.
function check(task, workspace, kind = "hidden") {
  workspace = NodeFS.realpathSync(workspace);
  const entry = NodeURL.pathToFileURL(NodePath.join(workspace, "solution.mjs")).href;
  const source =
    kind === "hidden"
      ? "import assert from 'node:assert/strict';\nimport * as api from " +
        JSON.stringify(entry) +
        ";\n" +
        task.hidden
      : task.publicFiles["public.test.mjs"].replace("'./solution.mjs'", JSON.stringify(entry));
  const result = NodeChildProcess.spawnSync(
    process.execPath,
    ["--permission", "--allow-fs-read=" + workspace, "--input-type=module"],
    { cwd: workspace, input: source, encoding: "utf8", timeout: 10_000, maxBuffer: 64 * 1024 },
  );
  return {
    passed: result.status === 0,
    exitCode: result.status,
    signal: result.signal,
    output: (result.error?.message ?? "") + (result.stderr ?? "") + (result.stdout ?? ""),
  };
}

export async function evaluate(task, workspace) {
  const integrity = {};
  for (const [name, content] of Object.entries(task.publicFiles)) {
    if (name !== "solution.mjs")
      integrity[name] = await NodeFSP.readFile(NodePath.join(workspace, name), "utf8").then(
        (x) => x === content,
        () => false,
      );
  }
  const solution = await NodeFSP.readFile(NodePath.join(workspace, "solution.mjs"), "utf8").catch(
    () => "",
  );
  const publicTest = check(task, workspace, "public");
  const hiddenTest = check(task, workspace);
  const changed = hash(solution) !== hash(task.publicFiles["solution.mjs"]);
  return {
    passed:
      changed && Object.values(integrity).every(Boolean) && publicTest.passed && hiddenTest.passed,
    changed,
    integrity,
    solutionSha256: hash(solution),
    publicTest,
    hiddenTest,
  };
}
