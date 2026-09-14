# Worker models and authentication

Read this when selecting a model, configuring authentication or diagnosing a missing model. The `ds_*` names and plugin ID remain unchanged for compatibility.

## Natural-language requests and scope

Use normal language with Codex; it translates the request into the plugin's typed parameters. No exact sentence or command syntax is required.

| Request | Meaning |
|---|---|
| “使用 OpenCode Go 的 GLM 5.3 Flash，推理开到最大” | Save `opencode-go / glm-5.3-flash / max` as this machine's default |
| “这次用官方 DeepSeek Flash，推理最高” | Override the current task's new workers with `deepseek / deepseek-flash / max`; keep the machine default |
| “当前默认用什么模型？” | Read and report the stored/effective default |
| “恢复插件原来的默认模型” | Reset the machine default to Go DeepSeek Flash/max |

“最大 / 最高 / 拉满 / max” maps to `max`; “高 / high” to `high`; “低 / low” to `low`; “默认档位 / 自动” to `null`. Distinguish a maximum request from `high`. Map provider/model aliases to exact catalog IDs and validate them. If a requested variant is unsupported, report available variants rather than silently lowering it.

“以后 / 全局 / 设为默认 / 所有新执行者” expresses a persistent default. “这次 / 当前任务” expresses a task override. Preserve any established user scope. By the chosen interaction convention, a standalone model/effort-setting request without a scope is a machine-default request; a model choice attached to a specific coding task is task-local unless the user says otherwise. The global setting covers this plugin's new workers on this machine, including future Codex tasks. It does not change the Codex controller model or rewrite existing workers.

## Persistent defaults

Use `ds_model_defaults` with `action="get"`, `"set"` or `"reset"`. On `set`, pass the intended provider/model/variant and optional workspace for catalog validation. Report the resolved selection and scope after saving. `get` reports the effective selection, whether it is saved, and the local settings path. `reset` restores the built-in selection even if the saved file is invalid.

The machine default is local state under the controller's state directory, separate from the plugin installation and provider credentials. It survives reinstalls and applies when subsequent checks/spawns omit model selection. No API keys are stored in it. Existing agents, queued messages and forks continue using their saved model selection.

Precedence is: explicit worker/task selection → machine default → built-in default. A variant-only override applies to the default model. A model-only override keeps the default provider; an explicit provider without a model selects that provider's built-in Flash model. A different explicit model/provider gets that model's own default variant unless supplied; it does not accidentally inherit another model's reasoning setting. Explicit `variant=null` always removes the override. Invalid saved settings cause an actionable error; never pretend the built-in model was the user's saved choice.

From the plugin directory:

```bash
node scripts/model-defaults.mjs get
node scripts/model-defaults.mjs set --provider opencode-go --model glm-5.3-flash --variant max
node scripts/model-defaults.mjs reset
```

Prefer natural language for ordinary use; these commands are useful for scripts, troubleshooting or verifying the saved values.

## Selection

Both `ds_check` and `ds_spawn_agent` accept `provider`, `model` and `variant`. `model` is the bare API model ID, without a provider prefix. `ds_check` also accepts `workspace` to read the same project configuration as the planned worker.

| Route | provider | model | Omitted variant |
|---|---|---|---|
| Go DeepSeek (built-in default) | `opencode-go` | `deepseek-v4.1-flash` | `max` |
| Go GLM | `opencode-go` | `glm-5.3-flash` | Runtime default |
| Official DeepSeek Flash | `deepseek` | `deepseek-flash` | Runtime default |
| Official DeepSeek Pro | `deepseek` | `deepseek-v4-pro` | Runtime default |

Without saved defaults, omitting `provider` selects Go and omitting `model` selects that provider's Flash model. Saved defaults take precedence for omitted selection fields as described above. Explicit `variant=null` omits the override for any model, including the old default. A string must appear in the local catalog's `available_variants`; unsupported variants fail before creating a session. The model list is not frozen: other IDs in either supported provider's configured OpenCode catalog can be selected.

OpenCode 1.18.30 listed `low/high/max` for Go GLM, Go DeepSeek Flash and official Flash; official Pro listed `high/max` when checked on 2026-09-14. Use `ds_check` for the current configuration instead of assuming every model supports `max`.

For example, include these fields alongside the normal `task`, `workspace`, `scope_paths` and instruction routing in a spawn:

```json
{ "provider": "opencode-go", "model": "glm-5.3-flash", "variant": "max" }
```

```json
{ "provider": "deepseek", "model": "deepseek-flash" }
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

- [OpenCode Go models and IDs](https://opencode.ai/docs/go/)
- [OpenCode provider authentication](https://opencode.ai/docs/providers/#deepseek)
- [OpenCode model configuration and variants](https://opencode.ai/docs/models/)
- [Current DeepSeek official model IDs and endpoint](https://api-docs.deepseek.com/quick_start/pricing/)

As of 2026-09-14, the official Flash ID is `deepseek-flash`; it differs from Go's `deepseek-v4.1-flash`. Official `deepseek-v4-pro` remains available. Recheck the current official model page before describing legacy aliases or retirement plans.
