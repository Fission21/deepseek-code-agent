# Worker lanes, models and authentication

Read this for detailed model/default selection or scope changes, external authentication, or a missing model. An ordinary native Luna task with the explicit defaults in SKILL.md does not require this reference. The `ds_*` names and plugin ID remain unchanged for compatibility.

## Natural-language requests and scope

Use normal language with Codex; it translates the request into the plugin's typed parameters. No exact sentence or command syntax is required.

| Request | Meaning |
|---|---|
| “这段实现用 Codex 原生 GPT-5.6 Luna，推理开到 medium” | Create a task-scoped native `gpt-5.6-luna` subagent with `reasoning_effort="medium"`; do not use `ds_*`, OpenCode or external credentials |
| “不要用外部 Provider，这次改用原生 Luna，推理拉满” | Create a task-scoped native `gpt-5.6-luna / max` subagent; fail explicitly if unavailable |
| “当前任务所有工蜂都用 5.6 Luna” | Use native `gpt-5.6-luna / max` for every new delegate in the current task; existing workers are not mutated |
| “本次全部工蜂使用 GLM 5.3 Flash” | Use `opencode-go / glm-5.3-flash / max` for every new OpenCode worker in the current task; keep the machine default |
| “使用 OpenCode Go 的 GLM 5.3 Flash，推理开到最大” | Save `opencode-go / glm-5.3-flash / max` as this machine's default |
| “这次用官方 DeepSeek Flash，推理最高” | Override the current task's new workers with `deepseek / deepseek-flash / max`; keep the machine default |
| “当前默认用什么模型？” | Read and report the stored/effective default |
| “恢复插件原来的默认模型” | Reset the machine default to Go DeepSeek Flash/max |

“最大 / 最高 / 拉满 / max” maps to `max`; “高 / high” to `high`; “中 / medium” to `medium`; “低 / low” to `low`; “默认档位 / 自动” removes an explicit override. When no effort is stated, native Luna must be spawned with `reasoning_effort="max"`, while an external provider/model selection must resolve an omitted `variant` to `max`. Distinguish a maximum request from `high`. Validate the selected lane's actual model and effort support. If unavailable, report it rather than silently changing effort, model or lane.

For the OpenCode lane, “以后 / 全局 / 设为默认 / 所有新执行者” expresses a persistent plugin-worker default, while “这次 / 当前任务” expresses a task override. A standalone external model/effort request without a scope is a machine-default request. That default covers only this plugin's future OpenCode workers; it does not change the Codex controller or native Luna.

The plugin cannot persist a Codex-native model default. A Luna request applies to the requested task or an explicitly established preference within the current Codex thread. Do not store Luna through `ds_model_defaults` or claim it will carry into future Codex tasks.

“所有工蜂 / 全部 worker” is a fleet-scoped model instruction, not a new provider type. Apply it to every delegate created after the instruction within the stated task/thread scope. Existing native subagents and OpenCode workers retain their saved models, so replace rather than mutate them when necessary. A fleet-scoped model instruction overrides the automatic lane recommendation. Native Luna can still use Codex's task-scoped message/follow-up queue, wait and interrupt controls, and it can work in an isolated Git worktree selected or prepared by Codex. Do not claim those orchestration capabilities belong to the model itself. OpenCode remains distinct when the task needs controller-persisted state discoverable across Codex tasks/restarts, OpenCode context forks, external-provider accounting, or persisted `task_spec` verification.

## Codex-native Luna lane

`gpt-5.6-luna` is a Codex-native subagent model, not an OpenCode catalog model or a third plugin provider. Select it through Codex's native subagent capability and pass `reasoning_effort="max"` when the user did not specify an effort. Pass an explicitly requested effort unchanged; if the user explicitly asks for the host's “default/automatic” effort, omit the field for that request. Do not call `ds_check`, `ds_spawn_agent`, or `ds_model_defaults`; do not start OpenCode; and do not read or forward `DEEPSEEK_API_KEY` or other external credentials.

The current host determines whether Luna and the requested effort are available. When unavailable, explain the limitation and keep the user's lane choice intact instead of falling back silently. A native subagent retains the model chosen when it was created; create a new subagent to change models. Codex still owns task design, authorization, diff review, independent verification and acceptance.

Keep the native agent ID for the current Codex task. When the host exposes native message/follow-up, wait and interrupt operations, reuse that agent for coherent correction turns, including queuing input while it is busy. Do not describe this task-scoped lifecycle as guaranteed recovery after an app restart unless the active Codex host explicitly provides that guarantee.

For isolation, use a host-native worktree/environment option when the native subagent interface exposes one. Otherwise Codex may create a dedicated Git worktree from the intended committed state before spawning and tell Luna to work only in that exact directory. A worktree does not include uncommitted changes; tasks that depend on them should remain in the current workspace with non-overlapping writes or run serially. Preserve reviewed changes before cleaning up a controller-created worktree. A user-visible Codex task/worktree is a separate workflow and may be created only when the user explicitly asks for a new task; do not use it as an implicit substitute for a hidden native worker.

## OpenCode worker persistent defaults

Use `ds_model_defaults` with `action="get"`, `"set"` or `"reset"`. On `set`, pass the intended provider/model/variant and optional workspace for catalog validation. Report the resolved selection and scope after saving. `get` reports the effective selection, whether it is saved, and the local settings path. `reset` restores the built-in selection even if the saved file is invalid.

The machine default is local state under the controller's state directory, separate from the plugin installation and provider credentials. It survives reinstalls and applies when subsequent checks/spawns omit model selection. No API keys are stored in it. Existing agents, queued messages and forks continue using their saved model selection.

Precedence is: explicit worker/task selection → machine default → built-in default. A variant-only override applies to the default model. A model-only override keeps the default provider; an explicit provider without a model selects that provider's built-in Flash model. A different explicit model/provider defaults to `max` when its variant is omitted; it does not accidentally inherit another model's saved reasoning setting. An explicitly saved variant for the same provider/model pair still wins, including saved `null`. Explicit `variant=null` always removes the override. Invalid saved settings cause an actionable error; never pretend the built-in model was the user's saved choice.

From the plugin directory:

```bash
node scripts/model-defaults.mjs get
node scripts/model-defaults.mjs set --provider opencode-go --model glm-5.3-flash --variant max
node scripts/model-defaults.mjs reset
```

Prefer natural language for ordinary use; these commands are useful for scripts, troubleshooting or verifying the saved values.

## OpenCode worker selection

Both `ds_check` and `ds_spawn_agent` accept `provider`, `model` and `variant`. `model` is the bare API model ID, without a provider prefix. `ds_check` also accepts `workspace` to read the same project configuration as the planned worker.

| Route | provider | model | Omitted variant |
|---|---|---|---|
| Go DeepSeek (built-in default) | `opencode-go` | `deepseek-v4.1-flash` | `max` |
| Go GLM | `opencode-go` | `glm-5.3-flash` | `max` |
| Official DeepSeek Flash | `deepseek` | `deepseek-flash` | `max` |
| Official DeepSeek Pro | `deepseek` | `deepseek-v4-pro` | `max` |

Without saved defaults, omitting `provider` selects Go and omitting `model` selects that provider's Flash model. Every omitted external `variant` resolves to `max`, including Go GLM and official Flash/Pro. Saved defaults take precedence for omitted selection fields as described above; an explicitly saved `null` remains `null`. Explicit `variant=null` omits the override for any model, including the old default. A string must appear in the local catalog's `available_variants`; unsupported variants fail before creating a session. The model list is not frozen: other IDs in either supported provider's configured OpenCode catalog can be selected.

OpenCode 1.18.30 listed `low/high/max` for Go GLM, Go DeepSeek Flash and official Flash; official Pro listed `high/max` when checked on 2026-09-14. Use `ds_check` for the current configuration instead of assuming every model supports `max`.

For example, include these fields alongside the normal `task`, `workspace`, `scope_paths` and instruction routing in a spawn:

```json
{ "provider": "opencode-go", "model": "glm-5.3-flash", "variant": "max" }
```

```json
{ "provider": "deepseek", "model": "deepseek-flash", "variant": "max" }
```

The selected route is saved per agent and returned by spawn, wait, inspect, list and fork. Immediate/queued follow-ups and forks use the same selection even after a controller restart. Old saved sessions retain the original Go DeepSeek/max selection. Start a new worker to change model. A provider error or exhausted quota never authorizes an automatic fallback.

## Connect locally

Install a current OpenCode version (verified here with 1.18.30). In the OpenCode TUI, use `/connect` and choose **OpenCode Go** or **DeepSeek**. Alternatively run `opencode auth login` and choose the provider there. Enter credentials only in OpenCode's local authentication flow.

The official route uses OpenCode's built-in `deepseek` provider, normally pointed at `https://api.deepseek.com`. OpenCode still manages tools, conversation history and reasoning content; model requests use the separately configured official account, not the Go subscription. User overrides to the provider endpoint remain OpenCode configuration and must be considered when verifying the actual destination.

For environment-based official authentication, supply `DEEPSEEK_API_KEY` to the Codex host before it starts. The plugin forwards this variable to OpenCode. Desktop apps do not necessarily inherit an interactive shell's environment, so `/connect` is usually simpler. Do not put credentials in tool arguments, prompts, manifests, logs or repository files.

CLI checks from the plugin directory:

```bash
npm run check
npm run check -- --model glm-5.3-flash --variant max
npm run check -- --provider deepseek
npm run check -- --provider deepseek --model deepseek-v4-pro --variant high
npm run check -- --model glm-5.3-flash --variant null --workspace /path/to/project
```

`model_available` describes the runtime catalog; `provider_connected` describes local provider configuration. `ok=true` requires both plus a valid variant and Git, but **does not verify the key, balance, quota or a successful inference**. The check starts the local OpenCode server and does not call a model. Never return provider configuration objects or keys in diagnostic output.

If the provider is disconnected, connect the chosen account. If a model is missing, update OpenCode and run `opencode models --refresh`; use `opencode models deepseek` or `opencode models opencode-go` to inspect the IDs. For a newly released official model not yet catalogued, configure its actual API ID under `provider.deepseek.models` with its capabilities/limits and supported variants in OpenCode. Do not invent an alias or silently substitute a different model.

## Sources

- [OpenAI GPT-5.6 Luna model](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [OpenAI Agents subagent messages and lifecycle items](https://developers.openai.com/api/reference/typescript/resources/beta/subresources/agents/subresources/sessions/subresources/subagents/subresources/items/methods/list)
- [OpenCode Go models and IDs](https://opencode.ai/docs/go/)
- [OpenCode provider authentication](https://opencode.ai/docs/providers/#deepseek)
- [OpenCode model configuration and variants](https://opencode.ai/docs/models/)
- [Current DeepSeek official model IDs and endpoint](https://api-docs.deepseek.com/quick_start/pricing/)

As of 2026-09-14, the official Flash ID is `deepseek-flash`; it differs from Go's `deepseek-v4.1-flash`. Official `deepseek-v4-pro` remains available. Recheck the current official model page before describing legacy aliases or retirement plans.
