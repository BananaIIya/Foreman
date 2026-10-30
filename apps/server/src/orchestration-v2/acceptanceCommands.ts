import type { AdvisorAcceptanceCommand } from "@t3tools/contracts";

/**
 * Acceptance commands come from model output: the advisor's plan, or in
 * worker-first mode the worker's own report. The server runs them only on
 * full-access threads (see AdvisorPipelineService). This policy is defense in
 * depth on top of that: each command is one executable started without a
 * shell, so it refuses shells and other ways to run a command string, network
 * clients, privilege changes, deletion, and executables outside the workspace.
 * Test runners such as node or pnpm still run workspace code; the thread's
 * runtime mode, not this list, is what allows that.
 */
const DENIED_EXECUTABLES = new Set([
  // Shells and command-string runners.
  "sh",
  "bash",
  "zsh",
  "fish",
  "dash",
  "ksh",
  "csh",
  "tcsh",
  "cmd",
  "powershell",
  "pwsh",
  "osascript",
  "env",
  "xargs",
  "eval",
  "exec",
  "nohup",
  // Network clients.
  "curl",
  "wget",
  "nc",
  "ncat",
  "netcat",
  "ssh",
  "scp",
  "sftp",
  "rsync",
  "ftp",
  "telnet",
  // Privilege changes and deletion.
  "sudo",
  "su",
  "doas",
  "rm",
  "rmdir",
  "dd",
]);

// Windows resolves these through cmd.exe or a script host, which is a shell.
const SCRIPT_EXTENSION = /\.(?:cmd|bat|ps1|vbs|js|sh)$/i;
const EXECUTABLE_EXTENSION = /\.(?:exe|com)$/i;
// A bare command that resolves to a .cmd shim (npm, pnpm) runs through cmd.exe
// with escaped arguments. Refuse the characters cmd.exe still interprets there.
const WINDOWS_SHELL_CHARACTERS = /[%!^"\r\n]/;

/**
 * Why the server will not run this command, or null when it may. Pass the
 * host platform when running; parsing checks only what holds everywhere.
 */
export function acceptanceCommandProblem(
  command: typeof AdvisorAcceptanceCommand.Type,
  platform?: NodeJS.Platform,
): string | null {
  const executable = command.command;
  const parts = executable.split(/[\\/]/);
  if (
    executable.startsWith("/") ||
    executable.startsWith("\\") ||
    /^[A-Za-z]:/.test(executable) ||
    parts.includes("..")
  ) {
    return `Acceptance command \`${executable}\` must be a command name or a workspace-relative path.`;
  }
  if (SCRIPT_EXTENSION.test(executable)) {
    return `Acceptance command \`${executable}\` is a script; run it with its interpreter instead.`;
  }
  const name = parts.at(-1)!.toLowerCase().replace(EXECUTABLE_EXTENSION, "");
  if (DENIED_EXECUTABLES.has(name)) {
    return `Acceptance command \`${executable}\` is not allowed: use a test runner or build tool, not a shell, network, privilege or deletion command.`;
  }
  if (
    [executable, ...command.args].some((part) => part.includes("\0")) ||
    (platform === "win32" && command.args.some((arg) => WINDOWS_SHELL_CHARACTERS.test(arg)))
  ) {
    return `Acceptance command \`${executable}\` has arguments the server cannot pass safely.`;
  }
  return null;
}

/** The first problem among commands, for feedback to the model that wrote them. */
export function acceptanceCommandsProblem(
  commands: ReadonlyArray<typeof AdvisorAcceptanceCommand.Type>,
  platform?: NodeJS.Platform,
): string | null {
  for (const command of commands) {
    const problem = acceptanceCommandProblem(command, platform);
    if (problem !== null) return problem;
  }
  return null;
}

const PASSED_VARIABLE =
  /^(?:PATH|PATHEXT|HOME|USERPROFILE|USER|USERNAME|LOGNAME|LANG|LANGUAGE|LC_[A-Z_]+|TZ|TERM|TMPDIR|TMP|TEMP|SYSTEMROOT|SYSTEMDRIVE|WINDIR|COMSPEC|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i;
const SECRET_VARIABLE = /KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION/i;

/**
 * Basic, non-secret variables only: the server's environment holds provider
 * credentials. ProcessRunner extends the host environment, so every other
 * host variable is set to undefined, which Node's spawn leaves out.
 */
export function acceptanceCommandEnvironment(
  host: Readonly<Record<string, string | undefined>>,
): Record<string, string | undefined> {
  return Object.fromEntries(
    Object.entries(host).map(([name, value]) => [
      name,
      PASSED_VARIABLE.test(name) && !SECRET_VARIABLE.test(name) ? value : undefined,
    ]),
  );
}
