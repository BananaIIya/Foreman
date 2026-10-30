# T3 Code: multi-model fork

This repository is a fork of [pingdotgg/t3code](https://github.com/pingdotgg/t3code), pinned at upstream commit `58d253231d83f669a2cdfefa5fe479751d22c582` and not tracking upstream after it. Everything below the "Upstream T3 Code" heading is upstream's README. The fork adds the five features below; the rest is upstream T3 Code.

| Added in this fork       | What it does                                                                                                                                                               | Where                                                    |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| Worker role              | A thread records which provider and model executes it, switchable by configuration.                                                                                        | `packages/contracts`, `apps/server/src/orchestration-v2` |
| Advisor pipeline         | A worker tries the task first; failed acceptance checks or a request for help escalate to a stronger advisor model, which writes a bounded plan. Optional plan-first mode. | [Composer guide](docs/user/composer.md#advisor-mode)     |
| Lead + sidekick          | A strong lead model hands self-contained briefs to a cheaper sidekick and checks its reports.                                                                              | [Composer guide](docs/user/composer.md#lead--sidekick)   |
| Usage by thread and role | Per-turn token ledger in SQLite, priced from a bundled `pricing.json`, grouped by thread and role on the Usage page, with CSV export.                                      | `apps/server/src/usage`                                  |
| Nebius provider          | Opt-in NVIDIA Nemotron 3 models through Nebius Token Factory, reusing the OpenCode adapter. Off by default.                                                                | [Worker and Nebius guide](docs/worker-nebius.md)         |

**Data directory.** The fork stores its data under `~/.t3-nvidia`, not upstream's `~/.t3`. Its database carries fork-only migrations, so it must never share a database with an official T3 Code install. Pass `--home-dir` or set `T3CODE_HOME` to choose another location.

**Acceptance checks.** The advisor pipeline runs the acceptance commands that a plan or worker report names. The server runs them only for threads in full-access mode, refuses shells, network tools and paths outside the workspace, and runs them without the server's credentials in the environment.

**Run from source.** The install commands in the upstream section install official T3 Code, not this fork. Install Node 24 and [`vp`](#install-vp), authenticate at least one provider, then run from the repository root:

```bash
vp i
vp run dev
```

The `[dev-runner]` line prints the web URL. To enable Nemotron, open Settings → Providers → Nebius, paste a Nebius API key and turn the instance on.

**Not supported in this fork.** Desktop and mobile store builds, auto-update, and release workflows remain upstream's and are not published from this repository.

**Experiments.** Benchmark reports and raw data live in [benchmarks/](benchmarks/README.md). Dollar figures there are API-equivalent prices for subscription usage, not billed amounts. The M0 decision record is [docs/decision-m0.md](docs/decision-m0.md).

**License.** MIT, unchanged from upstream. Copyright for upstream code remains with T3 Tools Inc.; see [LICENSE](LICENSE).

---

# Upstream T3 Code

T3 Code is an "agent harness control surface". It enables control of the agents on your machine with a best-in-class mobile app ([iOS](https://apps.apple.com/us/app/t3-code-remote-claude-more/id6787819824), [Android](https://play.google.com/store/apps/details?id=com.t3tools.t3code)), [web app](https://app.t3.codes) and [Electron-based desktop app](https://t3.codes).

Works with your subscriptions on Claude Code, Codex, Cursor, Grok Build, OpenCode, and Google Antigravity. If they're set up on your computer, T3 Code can control them.

## "Wait, what are you selling me?"

Nothing. We built T3 Code because we wanted the best possible development experience with agents. We were inspired by existing solutions like the Codex desktop app, Conductor, Claude Desktop and Cursor Glass, but none met our bar.

We wanted something performant, remote-ready, and truly open. If we ever go the wrong direction, we want you to have everything you need to fork and build the editor that you want.

## Installation

> [!WARNING]
> T3 Code currently supports Codex, Claude, Cursor, Grok Build, OpenCode, and Antigravity. Install and authenticate at least one provider before use:
>
> - Codex: install [Codex CLI](https://developers.openai.com/codex/cli) and run `codex login`
> - Claude: install [Claude Code](https://claude.com/product/claude-code) and run `claude auth login`
> - Cursor: install [Cursor CLI](https://cursor.com/cli) and run `agent login`
> - Grok Build: install [Grok Build CLI](https://x.ai/cli) and run `grok login`
> - OpenCode: install [OpenCode](https://opencode.ai) and run `opencode auth login`
> - Antigravity: enable it in Settings, then use **Install Antigravity** and **Sign in with Google**. No CLI is required.

### Command line

```bash
curl -fsSL https://t3.codes/install.sh | sh
```

On Windows, in PowerShell:

```powershell
irm https://t3.codes/install.ps1 | iex
```

Then run `t3` to start the server and open the local web app. `t3 service install` keeps it running in the background, `t3 update` moves to a newer release, and `t3 --help` has the full reference.

To try it once without installing, run `npx t3@latest` instead.

### Desktop app

Install the latest version of the desktop app from [GitHub Releases](https://github.com/pingdotgg/t3code/releases), or from your favorite package registry:

#### Windows (`winget`)

```bash
winget install T3Tools.T3Code
```

#### Windows (`scoop`)

```bash
scoop install extras/t3code
```

#### macOS (Homebrew)

```bash
brew install --cask t3-code
```

#### Debian, Ubuntu (`.deb`)

Download the `.deb` from [GitHub Releases](https://github.com/pingdotgg/t3code/releases), then:

```bash
sudo apt install ./T3-Code-*.deb
```

#### Arch Linux (AUR)

Stable:

```bash
yay -S t3code-bin
```

Nightly:

```bash
yay -S t3code-nightly-bin
```

The AUR packaging is maintained in this repository under [`packaging/aur`](./packaging/aur).

## Some notes

We are very very early in this project. Expect bugs.

We are (mostly) not accepting contributions yet. Small fixes may be considered. Big features will not be.

## Documentation

Full docs live in [docs/](./docs). There's no docs site yet.

- [Install and first run](./docs/user/install.md)
- [Permission modes](./docs/user/permission-modes.md)
- [Keyboard shortcuts](./docs/user/keybindings.md)
- [Project settings](./docs/user/project-settings.md)
- [Appearance preferences](./docs/user/appearance.md)
- [Remote access from a phone or another machine](./docs/user/remote-access.md)
- [Connect Claude Code, Codex, ChatGPT and other agents over MCP](./docs/user/outside-agents.md)
- [Keeping app and server in sync](./docs/user/updating.md)
- [Source control integrations](./docs/user/source-control.md)
- Multiple accounts: [Codex](./docs/user/providers-codex.md) · [Claude](./docs/user/providers-claude.md)
- [Run T3 Code as a background service](./docs/user/background-service.md)

Building from source? Start at [docs/internals/overview.md](./docs/internals/overview.md).

## If you REALLY want to contribute still.... read this first

### Install `vp`

T3 Code uses Vite+ so you'll need to install the global `vp` command-line tool.

#### macOS / Linux

```bash
curl -fsSL https://vite.plus | bash
```

#### Windows

```bash
irm https://vite.plus/ps1 | iex
```

Checkout their getting started guide for more information: https://viteplus.dev/guide/

### Install dependencies

```bash
vp i
```

Read [CONTRIBUTING.md](./CONTRIBUTING.md) before reporting a bug or opening a PR.

Have a feature request? Start an [Ideas discussion](https://github.com/pingdotgg/t3code/discussions/categories/ideas).

Need support? Join the [Discord](https://discord.gg/jn4EGJjrvv).
