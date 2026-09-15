# DeepSeek Code Agent for Codex

English | [简体中文](./README.zh-CN.md)

**Evidence status:** This is an optional delegation experiment, not a proven way to reliably reduce controller tokens. Keep tightly coupled reading and diagnosis with Codex by default. One read-heavy pair observed 5.25% fewer queen tokens but 62.01% longer elapsed time, with unresolved quality and experimental limitations; see the [results and caveats](./docs/reading-delegation-retest-2026-09-15.md). Compact bridge responses are a separate transport improvement, not proof of token savings.

Delegate bounded coding work either to Codex-native GPT-5.6 Luna or to DeepSeek/GLM through OpenCode. Codex remains the controller, reviewer, and final authority.

The bundled Skill chooses among direct Codex work, a native Luna subagent, and the plugin's persistent OpenCode worker. The plugin adds mailbox-style corrections, status waiting, context forks, permission handling, isolated Git worktrees, diff inspection, and scoped repository-instruction manifests for the OpenCode lane.

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
- A current OpenCode version (validated with 1.18.30) for the DeepSeek/GLM lane
- OpenCode Go access for Go models, or separate official DeepSeek API credentials, when using that lane

The Codex-native Luna lane uses the host's `gpt-5.6-luna` subagent directly. It does not need OpenCode, `DEEPSEEK_API_KEY`, or another external provider credential. Availability depends on the current Codex host and account.

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

## Select the worker lane and model

Use ordinary language in Codex. For example, “Use the Codex-native GPT-5.6 Luna subagent for this bounded implementation” selects the native lane and, when no effort is stated, Codex must spawn it with `model="gpt-5.6-luna"` and `reasoning_effort="max"`. It does not call `ds_check`, `ds_spawn_agent`, or OpenCode, and it is not a value accepted by the plugin's `provider` field. Luna reasoning can be explicitly requested as `none`, `low`, `medium`, `high`, `xhigh`, or `max`; an explicit “default/automatic” request intentionally omits the effort field. If the host does not offer the requested combination, Codex reports that instead of silently switching lanes.

The native Luna choice is scoped to the requested work. `ds_model_defaults` stores only OpenCode worker defaults and cannot persist a native Codex model preference. A native subagent uses the model selected when it is created; changing it means creating a new subagent.

You can also set one model for a group of delegates in ordinary language: “Use GPT-5.6 Luna for all workers in this task” makes every subsequently created delegate in that task a native Luna subagent; “Use Go GLM 5.3 Flash for all workers in this task” applies that external selection to each new OpenCode worker without changing the machine default. Existing workers keep their original model and must be replaced when the selection changes. “All workers” is task/thread scoped unless you explicitly say “future/global/all new workers”; native Luna still cannot be persisted through `ds_model_defaults`.

Luna does not need OpenCode to support ordinary correction loops or isolated coding. Codex can retain a native subagent within the current task, queue or send follow-ups through the host's native coordination controls, wait or interrupt it, and assign it a host-managed or controller-prepared Git worktree. These are Codex orchestration capabilities, not Luna model features. Cross-task/restart discovery of controller state, OpenCode context forks, external-provider accounting and persisted `task_spec` verification remain reasons to choose the plugin's OpenCode worker.

[OpenAI documents GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) as a cost-sensitive, high-volume model. Published API prices do not establish how a Codex plan meters native subagent usage, so this project does not claim a fixed Luna saving without a matched Codex measurement.

For the OpenCode worker lane, without saved machine preferences, the built-in default remains `opencode-go/deepseek-v4.1-flash` with `max` reasoning. `ds_check` and `ds_spawn_agent` accept:

| Route | `provider` | `model` |
|---|---|---|
| Go DeepSeek | `opencode-go` | `deepseek-v4.1-flash` |
| Go GLM | `opencode-go` | `glm-5.3-flash` |
| Official Flash | `deepseek` | `deepseek-flash` |
| Official Pro | `deepseek` | `deepseek-v4-pro` |

A standalone OpenCode model/effort-setting request saves that lane's machine default; use “this task only” for a temporary override. Use natural language: “Make OpenCode Go GLM 5.3 Flash with maximum reasoning my machine default,” “Use official DeepSeek Flash just for this task,” “Show my OpenCode worker defaults,” or “Restore the plugin's original defaults.” Maximum/最高/拉满 map to `max`.

Persistent defaults are saved through `ds_model_defaults`, apply to future workers across Codex tasks on this machine, and survive plugin reinstall. Task-specific choices override them without changing the saved preference. This setting controls plugin workers; the Codex controller model remains an app setting. The selected model persists across follow-ups, queues, restarts and forks. Changing model starts a new worker; errors never silently change providers.

`variant` is optional. The saved model pair inherits its saved variant. When explicitly switching to another provider/model pair without a variant, every external model defaults to `max`, including Go GLM and official Flash/Pro. An explicitly saved variant for the same pair still wins, including saved `null`; an explicit `variant=null` always uses the runtime default for that request. A string must be supported by that model's local catalog. Other model IDs configured in either provider's OpenCode catalog are also supported.

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

To use the native low-cost lane without external credentials:

```text
Use Codex-native GPT-5.6 Luna with maximum reasoning for the bounded implementation. Do not use OpenCode or external provider credentials; Codex must review and verify the result.
```

For native Luna, Codex sends a bounded task and the necessary repository context through native delegation. For the OpenCode lane, Codex passes explicit `scope_paths`, task-specific `required_reads`, and compact `critical_constraints`. In both cases Codex independently reviews the resulting diff and tests.

## Queen token efficiency

### Route by expected benefit and task complexity

Choose the route from expected queen work saved, risk, and exploration/verification burden, not line or file counts. **Small** known edits and tightly coupled reading or diagnosis stay with Codex by default, especially if review would repeat most of the investigation. **Medium** repetitive implementation or independent evidence batches are candidates for delegation when the labor removed from the queen is expected to outweigh dispatch, review and corrections within the time constraints. If that benefit is unclear, work directly; neither reading volume, a cheaper model nor extra parallel slots is sufficient, and no routing benchmark is required. Once delegation is justified, use Codex-native `gpt-5.6-luna` for bounded, low-risk native subtasks when available, including task-scoped correction loops and isolated worktree execution managed by Codex. Prefer the DeepSeek/GLM OpenCode worker when the user selects it or the task materially needs cross-task/restart discovery, controller-persisted state, OpenCode context forks, external-provider usage accounting, or persisted `task_spec` verification. **Large or high-risk** work — state machines, concurrency, permissions, transactions, migrations — may delegate only bounded labor to either lane. Codex keeps the interpretation of the user's request, consequential decisions, and final acceptance; before any code-changing delegation it checks the key contracts and design invariants that bound the implementation. When read-only delegation is justified, a full duplicate call-chain read is not required before dispatch. A worker's self-report or passing self-tests are never acceptance. An explicit per-task or fleet-wide model/lane choice overrides these defaults, and failures never authorize a silent cross-lane fallback.

A 2026-09-15 pilot paired one direct and one delegated DeepSeek execution per size from the same commit: [task routing pilot](./docs/task-routing-pilot-2026-09-15.md). It did not test Luna. It ran one execution per cell in parallel without alternating order, so it is not the repeated benchmark the queen protocol asks for, and its numbers neither generalize nor promise a fixed saving. In that pilot, delegated worker non-cached input plus output was 13.78% below the three direct runs, while the controller's own interval was 55.41% below the direct proxy total; the controller interval also covers managing the direct Codex control arm and reviewing both runs, so it is not a pure delegation bill. Worker totals including cached reads were higher than direct totals, so combined tokens and latency can still increase; medium and large worker usage is marked incomplete.

### Design, delegate, verify, review

Codex keeps user-request interpretation, consequential design choices and final acceptance. When the routing decision supports delegation, a worker may first do bounded read-only discovery, indexing or document gathering. For example: “Read the relevant service and tests; return a scoped evidence packet with conclusions, file/test locations, short excerpts and anything you did not verify. Do not edit.” The packet identifies the scope/version it covers. Before code changes, Codex checks the key contracts and design invariants, then gives the implementation a bounded objective and acceptance cases. A native Luna subagent receives that task through Codex's native delegation path; an OpenCode worker can additionally receive a persisted `task_spec`, worktree and exact check commands. Routine reading and implementation do not need step-by-step reports. Escalate meaningful blockers and batch ordinary issues. Codex reviews the actual patch and independently checks risk-critical paths and gaps without repeating all exploration or accepting a summary on its own; the OpenCode correction lifecycle remains unchanged.

`ds_verify_agent` runs only the persisted checks while the worker is idle, retaining exit codes and full logs. Verification is bound to the current source, Git index, task specification and instruction identity; edits invalidate earlier evidence. Repeated calls observe the same asynchronous job; `rerun=true` explicitly requests another execution. `ds_wait_agent(return_on="actionable")` absorbs transient retries and escalates a continuous 120-second retry streak. Use host-supported bounded waits: the 55-second wait and 65-second host timeout remain, and a timeout may require another bounded wait. Do not promise zero polling, restart-proof completion, or background wakeups. Existing calls without the new options keep their prior behavior.

See the [task and evidence contract](plugins/deepseek-code-agent/skills/deepseek-coding-delegation/references/control-contract.md). The original implementation update was locally functionally tested without a model comparison at that time; any later dated pilot or retest is reported separately and must not be turned into a timeless claim.

The default workflow reserves **queen (Codex) judgment** for intent, consequential design choices, uncertain diagnoses, risk-focused review and acceptance. Where delegation is justified, Luna or the OpenCode worker handles scoped discovery, editing, tests, routine fixes, documentation and evidence preparation. Selective reading, batching tool output and concise reporting also benefit direct Codex work and do not require a worker. The optimization target is the controller's total tokens (`input + output`); if reported input already includes cached input, do not add cached input again. Uncached input plus output is a separate proxy, and worker usage is reported separately; combined tokens and latency need not be lower. Delegate a coherent unit of work; reuse persistent OpenCode sessions for corrections, while native Luna gets a new subagent when its model selection changes. Do not delegate just because work is read-heavy; choosing a worker model does not require creating a worker.

`ds_wait_agent` and `ds_inspect_agent` default to compact responses: status, attention requests, cumulative worker usage and a bounded final handoff. Task echoes, intermediate tool logs and repeated instruction manifests stay out of the queen's normal context. Use `detail="full"` for diagnosis or omitted evidence and `include_diff=true` when the diff is needed. Save the spawn manifest once and reuse returned cursors.

Smaller tool responses are a measurable transport improvement, **not proof of end-to-end token savings**. The bridge cannot read the queen's usage. Compare matched direct/delegated tasks using actual queen usage, include failed attempts and review/correction turns, and keep worker tokens separate. See [the queen benchmark protocol](./docs/queen-token-efficiency.md).

The [earlier 27-result synthetic benchmark](./docs/token-routing-benchmark-2026-09-14.md) measured combined Codex + DeepSeek tokens and found delegation more expensive at every task size. Those results remain valid for that setup; they do not establish the queen-only savings of this version. Combined tokens and latency may still increase.

## Safety model

- Codex owns scope, authorization, business decisions, review, verification, and goal completion.
- Neither a native Luna subagent nor an OpenCode worker may commit, push, merge, deploy, alter production data, expose credentials, or discard unrelated changes without the user's authorization.
- Native Luna uses Codex's built-in delegation path and must not receive OpenCode provider fields or external credentials. A lane failure never permits silent fallback to another model or provider.
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
