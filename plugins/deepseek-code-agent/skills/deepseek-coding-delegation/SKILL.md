---
name: deepseek-coding-delegation
description: Keep the queen (Codex) on quality-critical engineering decisions while DeepSeek V4.1 Flash handles bounded discovery, implementation, tests, routine fixes and evidence preparation. Use for coding tasks, coding goals and their supporting engineering work; handle tiny known edits directly.
---

# DeepSeek Coding Delegation

Use the queen where its judgment most affects quality: intent, acceptance criteria, architecture and consequential tradeoffs, uncertain failure diagnosis, risk-focused review and final acceptance. DeepSeek is the worker: own high-volume investigation, implementation and evidence preparation within the agreed bounds. The objective is to preserve the queen’s capability while offloading labor, not to minimize queen involvement at the expense of quality. Combined tokens and latency can increase; do not promise measured savings without a comparable baseline.

## Divide judgment from labor

- Queen: decide what is correct, which constraints matter, consequential design choices and whether the evidence supports acceptance. Spend attention on unresolved uncertainty and risky changes.
- DeepSeek: search files, trace calls, inventory dependencies, compare existing patterns, edit code and docs, add/run relevant tests, fix known failures, reduce logs and prepare a diff/evidence summary. Delegate these supporting tasks even when their output is investigation rather than a patch.
- For a blocking design/business choice, ask DeepSeek for the decision needed, options, tradeoffs and its recommendation, with source paths or test evidence. The queen decides; the worker resumes. Do not interrupt the queen for ordinary implementation choices already covered by scope.

## Route and delegate

- Handle a tiny, understood edit directly when describing it and reviewing it would take more work than doing it. Hand off a coherent behavior or investigation with acceptance criteria, rather than one function or tool command per turn.
- Prefer one persistent DeepSeek worker. Avoid additional queen-model workers for ordinary implementation. Reuse the worker for related corrections; use a fresh session for unrelated tasks.
- Before spawning, read applicable repository instructions and only enough entry-point code to establish scope and acceptance. Let DeepSeek discover the implementation details. Do not solve the task and then ask it to transcribe your solution.
- Use concrete `scope_paths`, task-relevant `required_reads` (named sections for large files), and a few non-secret `critical_constraints`. Send paths and observable requirements, not full source files or conversation history.
- Prefer `workspace_mode="worktree"` for work starting from HEAD. Use `current` when the worker needs uncommitted changes, after checking status and protecting unrelated edits. Explain that it edits the shared tree.

## Keep the queen loop small

1. Check prerequisites once per environment. Spawn with objective, allowed modules, acceptance criteria and required checks. Ask the worker to finish the assigned investigation or implement, test and fix within those bounds before returning a concise handoff: outcome, changed files, checks actually run, unresolved risks and manifest acknowledgement.
2. Keep the returned agent ID, worktree, cursor and instruction manifest. Wait using `timeout_ms=55000` and the latest cursor. Compact output is the default; intermediate progress is not an invitation to inspect logs or repeat the worker's exploration. On `needs_attention`, resolve the specific permission/question within existing authorization.
3. At completion, review the diff and acceptance evidence. Compare instruction acknowledgements to the saved manifest. Read surrounding source only where the diff, missing evidence or risk requires it. Run proportionate independent acceptance checks; do not repeat the worker's entire investigation. A worker's success claim is not proof of correctness.
4. For a concrete failure, send one focused correction with evidence and the required check to the same worker. Further retries should follow new evidence; if the same failure recurs, reconsider the approach rather than looping unchanged.
5. Preserve and apply the reviewed patch, then close the agent. Remove its worktree only after useful changes are preserved. Codex owns `/goal` completion after review and validation.

Use `ds_inspect_agent` with `detail="full"` only for missing/truncated evidence, errors, instruction acknowledgements or a specific diagnosis; `include_diff=true` explicitly requests the diff. Never echo large logs into queen messages just to summarize them again. Do not infer success from idle, a timeout or unavailable usage.

DeepSeek must not commit, push, merge, deploy, modify production data, expose secrets or discard unrelated edits without the user's authorization for that action. If delegation is unavailable, report the limitation and continue locally when authorized.

Read [control contract](references/control-contract.md) when instruction routing, cursor recovery, workspace modes or a lifecycle edge case needs clarification. It is not required reading on every ordinary delegation.
