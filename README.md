# DeepSeek Code Agent for Codex

English | [简体中文](./README.zh-CN.md)

Delegate bounded coding work to DeepSeek or GLM through OpenCode Go, or directly to the official DeepSeek API through the OpenCode runtime. Codex remains the controller, reviewer, and final authority.

The plugin adds persistent worker sessions, mailbox-style corrections, status waiting, context forks, permission handling, isolated Git worktrees, diff inspection, and scoped repository-instruction manifests. It is designed for coding goals where one worker implements and Codex reviews.

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
- A current OpenCode version (validated with 1.18.30)
- OpenCode Go access for Go models, or separate official DeepSeek API credentials

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

## Select the model

Without saved machine preferences, the built-in default remains `opencode-go/deepseek-v4.1-flash` with `max` reasoning. `ds_check` and `ds_spawn_agent` now accept:

| Route | `provider` | `model` |
|---|---|---|
| Go DeepSeek | `opencode-go` | `deepseek-v4.1-flash` |
| Go GLM | `opencode-go` | `glm-5.3-flash` |
| Official Flash | `deepseek` | `deepseek-flash` |
| Official Pro | `deepseek` | `deepseek-v4-pro` |

A standalone model/effort-setting request saves the machine default; use “this task only” for a temporary override. Use natural language: “Make OpenCode Go GLM 5.3 Flash with maximum reasoning my machine default,” “Use official DeepSeek Flash just for this task,” “Show my worker defaults,” or “Restore the plugin's original defaults.” Maximum/最高/拉满 map to `max`.

Persistent defaults are saved through `ds_model_defaults`, apply to future workers across Codex tasks on this machine, and survive plugin reinstall. Task-specific choices override them without changing the saved preference. This setting controls plugin workers; the Codex controller model remains an app setting. The selected model persists across follow-ups, queues, restarts and forks. Changing model starts a new worker; errors never silently change providers.

`variant` is optional. The saved model pair inherits its saved variant. When explicitly switching to another pair, only the original Go DeepSeek model defaults to `max`; other models use their runtime default. Explicit `null` uses the runtime default for any model. A string must be supported by that model's local catalog. Other model IDs configured in either provider's OpenCode catalog are also supported.

For official API access, use OpenCode `/connect → DeepSeek` (or `opencode auth login`). Alternatively pass `DEEPSEEK_API_KEY` to the Codex host before launch; the plugin forwards it to OpenCode. The built-in official provider normally requests `https://api.deepseek.com` using your separate DeepSeek account. OpenCode still runs the coding tools and manages conversation history. The plugin does not rewrite provider endpoints or store keys.

```bash
npm --prefix plugins/deepseek-code-agent run check -- --model glm-5.3-flash --variant max
npm --prefix plugins/deepseek-code-agent run check -- --provider deepseek
```

The check reports catalog availability and local provider configuration; it does not call a model or validate the key, balance or quota. See [model selection and troubleshooting](plugins/deepseek-code-agent/skills/deepseek-coding-delegation/references/model-selection.md) for project configuration, missing models and CLI examples. Model IDs are documented in [OpenCode Go](https://opencode.ai/docs/go/) and [DeepSeek's current model page](https://api-docs.deepseek.com/quick_start/pricing/).

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

## Queen token efficiency

### Route by task complexity

Choose the route from reasoning, risk, and exploration/verification burden, not line or file counts. **Small** known edits that take a few minutes stay with Codex. **Medium** mechanical work with clear acceptance criteria prefers one persistent worker. **Large or high-risk** work — state machines, concurrency, permissions, transactions, migrations — may still delegate the labor, but Codex must first fix the design, interfaces, invariants, failure semantics and acceptance cases, then review the actual diff and run independent verification; a worker's self-report or passing self-tests are never acceptance. An explicit user request to delegate or to stay direct overrides these defaults.

A 2026-09-15 pilot paired one direct and one delegated execution per size from the same commit: [task routing pilot](./docs/task-routing-pilot-2026-09-15.md). It ran one execution per cell in parallel without alternating order, so it is not the repeated benchmark the queen protocol asks for, and its numbers neither generalize nor promise a fixed saving. In that pilot, delegated worker non-cached input plus output was 13.78% below the three direct runs, while the controller's own interval was 55.41% below the direct proxy total; the controller interval also covers managing the native control arm and reviewing both runs, so it is not a pure delegation bill. Worker totals including cached reads were higher than direct totals, so combined tokens and latency can still increase; medium and large worker usage is marked incomplete.

### Design, implement, verify, review

Codex reads the critical call chain and decides interfaces, invariants and acceptance cases before delegation. An optional `task_spec` gives the worker those decisions and the exact check commands. The worker completes implementation, self-tests and routine repairs in one session. Codex reviews the patch and batches feedback; after two unsuccessful correction rounds it can take over after confirming the worker and verification have stopped.

`ds_verify_agent` runs only the persisted checks while the worker is idle, retaining exit codes and full logs. Verification is bound to the current source, Git index, task specification and instruction identity; edits invalidate earlier evidence. Repeated calls observe the same asynchronous job; `rerun=true` explicitly requests another execution. `ds_wait_agent(return_on="actionable")` absorbs transient retries and escalates a continuous 120-second retry streak. The 55-second wait and 65-second host timeout remain. Existing calls without the new options keep their prior behavior.

See the [task and evidence contract](plugins/deepseek-code-agent/skills/deepseek-coding-delegation/references/control-contract.md). This update has local functional tests; no new model comparison was run, so token savings and latency improvements remain unmeasured.

The default workflow reserves **queen (Codex) judgment** for intent, consequential design choices, uncertain diagnoses, risk-focused review and acceptance. The worker handles high-volume discovery, editing, tests, routine fixes, documentation and evidence preparation within scope. Queen token savings should come from offloading labor while preserving quality. Delegate a coherent unit of work and reuse its session for corrections. Tiny known edits can still cost less to do directly.

`ds_wait_agent` and `ds_inspect_agent` default to compact responses: status, attention requests, cumulative worker usage and a bounded final handoff. Task echoes, intermediate tool logs and repeated instruction manifests stay out of the queen's normal context. Use `detail="full"` for diagnosis or omitted evidence and `include_diff=true` when the diff is needed. Save the spawn manifest once and reuse returned cursors.

Smaller tool responses are a measurable transport improvement, **not proof of end-to-end token savings**. The bridge cannot read the queen's usage. Compare matched direct/delegated tasks using actual queen usage, include failed attempts and review/correction turns, and keep worker tokens separate. See [the queen benchmark protocol](./docs/queen-token-efficiency.md).

The [earlier 27-result synthetic benchmark](./docs/token-routing-benchmark-2026-09-14.md) measured combined Codex + DeepSeek tokens and found delegation more expensive at every task size. Those results remain valid for that setup; they do not establish the queen-only savings of this version. Combined tokens and latency may still increase.

## Safety model

- Codex owns scope, authorization, business decisions, review, verification, and goal completion.
- The worker must not commit, push, merge, deploy, alter production data, expose credentials, or discard unrelated changes.
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
