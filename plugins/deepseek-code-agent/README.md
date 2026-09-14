# DeepSeek Code Agent for Codex

This personal Codex plugin keeps Codex as the controller and reviewer while DeepSeek V4.1 Flash Max implements bounded coding tasks through OpenCode Go.

The plugin contains:

- a local MCP controller with persistent sessions, mailbox-style follow-ups, waiting, inspection, context forks, permission replies, interruption, and isolated Git worktrees;
- scoped instruction routing that discovers applicable `AGENTS.md` files, hashes Codex-selected rule files, and avoids sending unrelated policy documents;
- an implicitly discoverable Skill for coding tasks and active `/goal` coding objectives;
- no API keys or machine-specific credentials.

## First computer

1. Install OpenCode and confirm `opencode --version` works.
2. Authenticate the `opencode-go` provider locally. Never store the key in this plugin or a repository.
3. Run `npm run check` in this directory.
4. Install the plugin from the personal marketplace and start a new Codex task so its tools and Skill are loaded.
5. Use `/goal` with a coding objective, or explicitly invoke `$deepseek-coding-delegation`.

## Another computer

Use the Codex plugin Share action, then install the shared plugin on the other computer. Install OpenCode and authenticate `opencode-go` separately on that computer. Credentials and local DeepSeek session history are intentionally not transferred.

After installation, run the plugin's `ds_check` tool or `npm run check`. Start a new Codex task before testing delegation.

## Local checks

```bash
npm test
npm run check
npm run test:live
```

The live test makes one small DeepSeek request in a temporary Git repository and removes the temporary worktree afterward.

## Routing expectation

Optimize queen (Codex) tokens by handing a coherent implementation unit to DeepSeek, including discovery, tests and fixes. Codex supplies acceptance criteria and reviews the patch. Compact wait/inspect responses are the default; request `detail="full"` only when specific diagnostic evidence is missing. Tiny edits can be handled directly.

Payload size is not end-to-end token usage. Measure actual queen tokens against a matched direct baseline, including failures and correction turns. Keep DS tokens, latency and quality separate; combined tokens may increase. See [the measurement protocol](../../docs/queen-token-efficiency.md) and the preserved historical benchmark in the repository.
