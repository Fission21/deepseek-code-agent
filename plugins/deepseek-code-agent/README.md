# DeepSeek Code Agent for Codex

This personal Codex plugin keeps Codex as the controller and reviewer while DeepSeek or GLM implements bounded coding tasks through OpenCode Go or the official DeepSeek API. OpenCode remains the agent runtime.

The plugin contains:

- a local MCP controller with persistent sessions, mailbox-style follow-ups, waiting, inspection, context forks, permission replies, interruption, and isolated Git worktrees;
- scoped instruction routing that discovers applicable `AGENTS.md` files, hashes Codex-selected rule files, and avoids sending unrelated policy documents;
- an implicitly discoverable Skill for coding tasks and active `/goal` coding objectives;
- no API keys or machine-specific credentials.

## First computer

1. Install OpenCode and confirm `opencode --version` works.
2. Authenticate the `opencode-go` or `deepseek` provider locally. Never store the key in this plugin or a repository.
3. Run `npm run check` in this directory.
4. Install the plugin from the personal marketplace and start a new Codex task so its tools and Skill are loaded.
5. Use `/goal` with a coding objective, or explicitly invoke `$deepseek-coding-delegation`.

## Another computer

Use the Codex plugin Share action, then install the shared plugin on the other computer. Install OpenCode and authenticate the selected provider separately on that computer. Credentials and local DeepSeek session history are intentionally not transferred.

After installation, run the plugin's `ds_check` tool or `npm run check`. Start a new Codex task before testing delegation.

## Model selection

The built-in default is `opencode-go/deepseek-v4.1-flash` with variant `max`; a saved machine preference takes precedence. Pass `model="glm-5.3-flash"` for Go GLM, or `provider="deepseek"` for official `deepseek-flash`. `deepseek-v4-pro` and other models in the selected provider's OpenCode catalog are also supported. The saved model pair inherits its saved variant; a different selected model uses its runtime default unless explicitly set; `variant=null` removes the override for any model.

`ds_model_defaults` gets, sets or resets the persistent machine default for future workers across Codex tasks. Natural language such as “make Go GLM 5.3 Flash with maximum reasoning my default” maps to a validated saved selection. `ds_check` and `ds_spawn_agent` inherit it when selection is omitted; explicit selections override it. It persists across queued follow-ups, restarts and forks. Checks report catalog/provider configuration without verifying a key or calling inference. Official API requests use separate DeepSeek credentials; connect through OpenCode `/connect`, or supply `DEEPSEEK_API_KEY` to the host before launch. There is no automatic provider fallback.

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

Optimize queen (Codex) tokens by handing a coherent implementation unit to the worker, including discovery, tests and fixes. Codex supplies acceptance criteria and reviews the patch. Compact wait/inspect responses are the default; request `detail="full"` only when specific diagnostic evidence is missing. Tiny edits can be handled directly.

Payload size is not end-to-end token usage. Measure actual queen tokens against a matched direct baseline, including failures and correction turns. Keep DS tokens, latency and quality separate; combined tokens may increase. See [the measurement protocol](../../docs/queen-token-efficiency.md) and the preserved historical benchmark in the repository.
