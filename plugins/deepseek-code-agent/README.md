# DeepSeek Code Agent for Codex

This personal Codex plugin keeps Codex as the controller and reviewer while bounded coding work runs either in a Codex-native GPT-5.6 Luna subagent or in a persistent DeepSeek/GLM worker through OpenCode. OpenCode remains the runtime only for the external worker lane.

The plugin contains:

- a local MCP controller with persistent sessions, mailbox-style follow-ups, waiting, inspection, context forks, permission replies, interruption, and isolated Git worktrees;
- scoped instruction routing that discovers applicable `AGENTS.md` files, hashes Codex-selected rule files, and avoids sending unrelated policy documents;
- an implicitly discoverable Skill for coding tasks and active `/goal` coding objectives;
- no API keys or machine-specific credentials.

## First computer

1. If you will use the external worker lane, install OpenCode and confirm `opencode --version` works.
2. For that lane, authenticate the `opencode-go` or `deepseek` provider locally. Never store the key in this plugin or a repository.
3. Before the first external worker, run `npm run check` in this directory. Native Luna does not require this provider check.
4. Install the plugin from the personal marketplace and start a new Codex task so its tools and Skill are loaded.
5. Use `/goal` with a coding objective, or explicitly invoke `$deepseek-coding-delegation`.

## Another computer

Use the Codex plugin Share action, then install the shared plugin on the other computer. Install OpenCode and authenticate the selected provider separately on that computer. Credentials and local DeepSeek session history are intentionally not transferred.

Before using an external worker on the other computer, run the plugin's `ds_check` tool or `npm run check`. Start a new Codex task before testing delegation.

## Worker lane and model selection

For a bounded, low-risk native task, ask Codex to “use the native GPT-5.6 Luna subagent.” If no effort is stated, Codex must create it with `model="gpt-5.6-luna"` and `reasoning_effort="max"`; an explicit effort such as `medium` is passed unchanged. That route uses the host's model directly and does not call `ds_check`, `ds_spawn_agent`, OpenCode, or an external credential. It is task-scoped, is not accepted as a plugin `provider`, and is not persisted by `ds_model_defaults`. Host availability is checked when Codex creates the native subagent; failure never silently switches lanes.

A fleet-scoped request can select one model for every new delegate in the current task or Codex thread. For example, “use GPT-5.6 Luna for all workers in this task” routes every subsequently created delegate to native Luna/max, while “use Go GLM 5.3 Flash for all workers in this task” applies that OpenCode selection without changing the machine default. Existing workers retain their original model and must be replaced to change it. “All workers” is not machine-global unless the user explicitly says “future/global/all new workers”; Luna cannot be persisted through `ds_model_defaults`.

Luna does not need OpenCode for an ordinary correction loop or isolated coding. Codex can retain a native subagent for the current task, queue or send follow-ups through native coordination controls, wait or interrupt it, and assign it a host-managed or controller-prepared Git worktree. Those are Codex orchestration capabilities, not Luna model features. Cross-task/restart discovery, persisted controller state, OpenCode context forks, external-provider accounting and persisted `task_spec` verification remain reasons to choose the OpenCode worker.

[OpenAI describes Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) as cost-sensitive and high-volume. API list prices are not a promise about native Codex plan metering, and this project has not benchmarked the Luna lane.

For the persistent OpenCode worker lane, the built-in default is `opencode-go/deepseek-v4.1-flash` with variant `max`; a saved machine preference takes precedence. Pass `model="glm-5.3-flash"` for Go GLM, or `provider="deepseek"` for official `deepseek-flash`. `deepseek-v4-pro` and other models in the selected provider's OpenCode catalog are also supported. The saved model pair inherits its saved variant; a different selected provider/model pair defaults to `max` when no variant is supplied; an explicit `variant=null` removes the override for that request.

`ds_model_defaults` gets, sets or resets the persistent machine default for future OpenCode workers across Codex tasks. Natural language such as “make Go GLM 5.3 Flash with maximum reasoning my default” maps to a validated saved selection. `ds_check` and `ds_spawn_agent` inherit it when selection is omitted; explicit selections override it. The selected OpenCode model persists across queued follow-ups, restarts and forks. Checks report catalog/provider configuration without verifying a key or calling inference. Official API requests use separate DeepSeek credentials; connect through OpenCode `/connect`, or supply `DEEPSEEK_API_KEY` to the host before launch. There is no automatic provider fallback.

See [model setup and examples](skills/deepseek-coding-delegation/references/model-selection.md).

## Local checks

```bash
npm test
npm run check
npm run check -- --model glm-5.3-flash --variant max
npm run check -- --provider deepseek
npm run test:live
npm run test:live -- --model glm-5.3-flash --variant low
```

The live test makes a small request to the selected model in a temporary Git repository and removes the temporary worktree afterward.

## Routing expectation

Route by expected queen work saved, risk, and exploration/verification burden, not line counts. Small known edits and tightly coupled reading or diagnosis stay with Codex by default. Repetitive implementation or independent evidence batches can justify delegation if the queen labor removed is expected to outweigh dispatch, review and corrections within time constraints. If benefit is unclear, work directly; reading volume, a cheaper model or parallel capacity alone is not enough, and no new benchmark is required to decide. Once delegation is justified, use native `gpt-5.6-luna` for bounded low-risk subtasks when available, including task-scoped correction loops and isolated worktree execution managed by Codex. Prefer the DeepSeek/GLM OpenCode lane when the user selects it or the task materially needs cross-task/restart discovery, controller-persisted state, OpenCode context forks, external-provider accounting, or persisted `task_spec` verification. Large or high-risk work (state machines, concurrency, permissions, transactions, migrations) may delegate only bounded labor to either lane. Codex retains user-request interpretation, consequential decisions and final acceptance; before code-changing delegation it checks the key contracts and design invariants that bound the work. For justified read-only delegation, do not require a full duplicate investigation before dispatch. Worker self-reports and passing self-tests are never acceptance. An explicit per-task or fleet-wide model choice overrides these defaults, and lane failures never cause silent fallback.

Codex retains the design, interfaces, invariants and acceptance cases that matter for implementation. When delegation is justified, a worker may first perform bounded read-only discovery or indexing. For example: “Read the relevant service and tests; return a scoped/versioned evidence packet with conclusions, file/test locations, short excerpts and unverified or uncertain items. Do not edit.” Before code changes, Codex checks the relevant contracts and invariants, then gives one bounded task to a native Luna subagent or a persistent OpenCode worker. Routine reading and implementation need no step-by-step report; meaningful blockers are escalated and ordinary issues are batched. Selective reading, batching and concise reporting also apply to direct Codex work without a worker. A worker-model preference does not require spawning an agent. Codex checks risk-critical paths and gaps in the actual patch rather than repeating all exploration or accepting the handoff summary alone. After two unsuccessful OpenCode review correction rounds it may take over, after confirming the worker and verification have stopped.

The repository's 2026-09-15 [task routing pilot](../../docs/task-routing-pilot-2026-09-15.md) records one direct/delegated DeepSeek pair per size from one commit; it did not test Luna. It is a single non-repeated pilot, not a benchmark; its results do not generalize, and combined tokens and latency can still increase. The link targets the source repository layout; a standalone plugin package does not include the `docs/` directory.

`ds_spawn_agent` accepts an optional structured `task_spec`. `ds_verify_agent` runs its preselected commands and preserves full logs, binding results to a source snapshot. Verification can run beyond one MCP wait window; repeated calls observe the same job rather than executing checks again. Inspect does not execute the configured checks. Neither worker completion nor passing checks means Codex has accepted the patch.

Use `return_on="actionable"` with `ds_wait_agent` to absorb transient provider retries. Continuous retries over 120 seconds become an attention event. Use host-supported bounded waits: the 55-second wait limit and 65-second host timeout remain, and normal timeouts may require another bounded wait. Do not promise zero polling, restart-proof completion or background wakeups. Legacy callers retain their previous behavior.

Payload size is not end-to-end token usage. The primary optimization measure is the controller total (`input + output`); when reported input already includes cached input, never add cached input again. Uncached input plus output is a separate proxy. Measure all correction or takeover turns, and keep worker tokens, latency and quality separate; lower combined totals are not a requirement. These token counters do not directly represent subscription quota. See [the control and evidence contract](skills/deepseek-coding-delegation/references/control-contract.md). Local automated tests do not prove token savings; live benchmarks are only run when requested.
