---
name: deepseek-coding-delegation
description: "Delegate implementation work to the stateful DeepSeek coding agent while Codex remains responsible for scope, review, corrections, and verification. Use for coding tasks and active /goal coding objectives that contain a bounded implementation subtask. Do not use for explanation-only requests or when the user explicitly asks Codex to implement without delegation."
---

# DeepSeek Coding Delegation

Use the `ds_*` MCP tools to treat DeepSeek V4.1 Flash Max as the implementation worker. Codex remains the controller: understand the request, define scope, inspect the result, request corrections, and run independent verification before reporting completion.

## When to delegate

- For an active `/goal` whose objective includes coding, delegate a bounded implementation unit when the DeepSeek tools are available.
- Prefer one DeepSeek worker. Do not also create a Codex model subagent unless the user explicitly asks for another model.
- Handle tiny edits directly when delegation would require more context than the implementation itself.
- Do not delegate analysis-only, review-only, status, or explanation requests unless implementation is also requested.

## Control loop

1. Read the applicable repository `AGENTS.md` and use its routing table to select only the task-relevant linked rules. Keep responsibility for business decisions and task boundaries.
2. Call `ds_spawn_agent` with a compact context capsule plus:
   - `scope_paths`: the bounded workspace-relative files or modules; the controller discovers their root-to-target `AGENTS.md` chain.
   - `required_reads`: only the additional rules selected in step 1. Use `{path, sections}` for large documents so the worker reads only the relevant sections; a plain path means the whole file. Do not send every repository rule.
   - `critical_constraints`: a short, loss-resistant summary of the task's non-negotiable constraints. Do not paste complete instruction files or credentials.
3. Use `workspace_mode="worktree"` when the task can start from `HEAD`. Use `workspace_mode="current"` only when the worker must see current uncommitted files; state that it will edit the shared working tree.
4. Use `ds_wait_agent` with the returned cursor. If it reports `needs_attention`, inspect the permission or question and respond only within the user's existing authorization.
5. Compare the worker's acknowledged instruction paths and hashes with the controller-generated `instruction_manifest`, then read the result and diff. The manifest proves which rule versions were selected, not that the model complied; verify claims independently with repository tools and proportionate tests.
6. If review finds a concrete problem, call `ds_send_message` on the same agent with evidence, the required correction, and focused regression checks. Prefer one correction round before replacing the approach.
7. Accept or apply only the reviewed patch. Never let the worker commit, push, merge, deploy, alter production data, or disclose secrets unless the user separately authorizes that exact action.
8. Close the agent when finished. Remove an isolated worktree only after its useful changes are preserved; never remove it merely to tidy up an unresolved task.

## Instruction routing

Use a hybrid protocol: Codex selects and summarizes; DeepSeek reads the selected source files. Repository files remain authoritative, while `critical_constraints` protects the few requirements most likely to be lost in a long document.

- Always provide concrete `scope_paths` when the task is narrower than the repository. Nested `AGENTS.override.md` takes precedence over `AGENTS.md` in the same directory.
- Add only rules that change implementation decisions to `required_reads`, normally one or two files for a bounded task. Select named sections when a file is large. Do not include unrelated database, UI, crawler, mobile, or release rules.
- For tiny edits, implement directly when the instruction and coordination overhead would exceed the code change.
- `workspace_mode="worktree"` hashes and exposes the committed `HEAD` versions. If an applicable rule or required source exists only as an uncommitted change, use `current` or do not delegate.
- A missing, escaping, absolute, or out-of-workspace instruction path must fail before model execution.

## `/goal` behavior

When a coding goal is active, keep the goal status owned by Codex. The DeepSeek session is subordinate to that goal and does not complete it. Mark the goal complete only after Codex has reviewed the patch, completed required verification, and confirmed no requested work remains.

If delegation is unavailable, continue the goal locally and report the tooling limitation instead of leaving the goal unfinished.

For tool meanings, state mapping, permission handling, and the context-capsule format, read [control contract](references/control-contract.md) when starting or resuming a delegated coding task.
