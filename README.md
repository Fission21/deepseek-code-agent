# DeepSeek Code Agent for Codex

English | [简体中文](./README.zh-CN.md)

Delegate bounded implementation work to DeepSeek V4.1 Flash through OpenCode Go while Codex remains the controller, reviewer, and final authority.

The plugin adds persistent DeepSeek sessions, mailbox-style corrections, status waiting, context forks, permission handling, isolated Git worktrees, diff inspection, and scoped repository-instruction manifests. It is designed for coding goals where one worker implements and Codex reviews.

## One-sentence installation with Codex

Paste this into a new local Codex task:

> Open <https://github.com/Fission21/deepseek-code-agent>, read the README, install this plugin on the current computer, run its prerequisite check, and never ask me to paste credentials into chat.

Codex can then follow the platform-specific prerequisites below and install the Git marketplace. Review any local command before allowing it if your Codex permission mode requires approval.

## Direct installation

These Codex commands are identical on Windows, macOS, and Linux:

```text
codex plugin marketplace add Fission21/deepseek-code-agent --ref main
codex plugin add deepseek-code-agent@deepseek-code-agent
```

Start a new Codex task after installation so the MCP tools and Skill are loaded.

## Requirements

- Local Codex desktop app or Codex CLI with plugin support
- Git
- Node.js 18 or newer
- OpenCode with access to the `opencode-go/deepseek-v4.1-flash` model
- An OpenCode Go account or compatible provider entitlement

The plugin stores no API keys. Authenticate OpenCode interactively on each computer:

```text
opencode auth login
opencode models opencode-go
```

Confirm that the model list contains:

```text
opencode-go/deepseek-v4.1-flash
```

Do not paste provider keys into Codex prompts, GitHub issues, logs, or repository files.

## Platform setup

### macOS

Install Git and Node.js using your normal package manager, then install OpenCode:

```bash
npm install -g opencode-ai@latest
opencode --version
```

### Linux

Install Git and Node.js 18+ using your distribution package manager or Node installer, then:

```bash
npm install -g opencode-ai@latest
opencode --version
```

### Windows

Install Git for Windows and Node.js 18+, open PowerShell, then:

```powershell
npm install -g opencode-ai@latest
opencode --version
```

The bridge detects the Windows npm `opencode.cmd` launcher and runs it through `cmd.exe`. If OpenCode is installed somewhere unusual, set `OPENCODE_BIN` to the full `.cmd`, `.bat`, or executable path before starting Codex.

OpenCode installation commands can change; check the [official OpenCode documentation](https://opencode.ai/docs/) if the npm command is no longer current.

## Verify the installation

In a new Codex task, ask:

> Run the DeepSeek Code Agent prerequisite check and report the detected platform, Node, Git, OpenCode, provider, model, and reasoning variant.

Or from this repository:

```bash
npm --prefix plugins/deepseek-code-agent test
npm --prefix plugins/deepseek-code-agent run check
```

The live test performs one small provider request and may consume quota:

```bash
npm --prefix plugins/deepseek-code-agent run test:live
```

## Usage

Create a coding goal in Codex, or explicitly invoke the bundled Skill:

```text
/goal Implement the requested feature. Keep Codex as controller and delegate the bounded implementation to DeepSeek.
```

```text
Use $deepseek-coding-delegation to delegate this implementation and review the result.
```

Codex selects the relevant repository instructions, passes bounded `scope_paths`, task-specific `required_reads`, and compact `critical_constraints`, then independently reviews the resulting diff and tests. Large rule documents can be limited to named sections instead of being copied into the prompt.

## Safety model

- Codex owns scope, authorization, business decisions, review, verification, and goal completion.
- DeepSeek must not commit, push, merge, deploy, alter production data, expose credentials, or discard unrelated changes.
- Isolated worktrees start from committed `HEAD`; they do not include uncommitted files.
- Instruction manifests identify the exact paths, hashes, and read scopes selected for the worker. They do not prove semantic compliance, so Codex must still review the patch.
- The local OpenCode server binds to `127.0.0.1` and uses a random per-process password.

## Development

The installable plugin lives in [`plugins/deepseek-code-agent`](./plugins/deepseek-code-agent). The repository root is also a Codex Git marketplace.

```bash
npm --prefix plugins/deepseek-code-agent test
python3 /path/to/plugin-creator/scripts/validate_plugin.py plugins/deepseek-code-agent
```

Tests run on Windows, macOS, and Linux in GitHub Actions. Provider-backed checks are intentionally excluded from public CI because credentials are never stored in the repository.

## License

[MIT](./LICENSE)
