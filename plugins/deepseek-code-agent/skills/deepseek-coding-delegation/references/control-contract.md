# DeepSeek control contract

Read this reference when starting, resuming, or diagnosing a delegated coding session.

## Context capsule

Keep the first message concise and operational:

```text
Objective:
Observed call chain and current behavior:
Allowed files or modules:
Acceptance criteria:
Required checks:
Do not:
Return:
```

Under `Return`, require changed files, design choices, commands actually run with results, and remaining risks. The worker can read repository files itself; do not paste large files into the message.

Pass instruction routing separately from the prose capsule:

```json
{
  "scope_paths": ["app/services/example.py", "tests/test_services/test_example.py"],
  "required_reads": [
    {
      "path": "docs/rules/coding-standards.md",
      "sections": ["类型与契约", "异常处理"]
    },
    {
      "path": "docs/rules/database-rules.md",
      "sections": ["查询与分页"]
    }
  ],
  "critical_constraints": [
    "Preserve the existing API response contract.",
    "Do not access a real external service from tests."
  ]
}
```

`scope_paths` drives automatic discovery of the applicable project `AGENTS.md` chain. In each directory, `AGENTS.override.md` wins over `AGENTS.md`. `required_reads` contains only additional files that Codex selected after reading the repository's routing instructions. A string selects the full file; `{path, sections}` selects named sections with enough surrounding context. The controller hashes the full source files in the actual agent workspace and returns an `instruction_manifest`; it does not copy their contents into the prompt.

The worker must acknowledge each manifest path and hash in its result. Codex compares that acknowledgement before review. This detects stale or mismatched instruction versions, but it does not prove semantic compliance.

## Tool lifecycle

- `ds_check`: verify Node, OpenCode, the OpenCode Go provider, and the requested model before the first delegation on a machine.
- `ds_spawn_agent`: create a persistent OpenCode session and immediately submit the initial task. Save `agent_id`, `cursor`, and `worktree` from the result.
- `ds_wait_agent`: long-poll for completion, retry, queued-message delivery, or requests that need attention. Pass the last cursor to avoid replaying old messages.
- `ds_send_message`: continue the same context. When the agent is busy, the bridge queues the message and sends it at the next idle boundary.
- `ds_inspect_agent`: retrieve status, recent text, usage, pending requests, and optionally the diff.
- `ds_fork_agent`: fork the conversation from its current or specified message. A fork shares the same filesystem, so do not run parent and child concurrently.
- `ds_reply_agent`: answer a permission or question request. Prefer one-time permission; reject requests outside the task scope.
- `ds_interrupt_agent`: stop the current turn while preserving the session.
- `ds_close_agent`: abort current work and mark the bridge record closed. Set `remove_worktree=true` only after preserving useful changes.
- `ds_list_agents`: recover local agent IDs after a Codex restart.

## State mapping

| Bridge state | Meaning | Controller action |
|---|---|---|
| `running` | DeepSeek is working | Wait using the returned cursor |
| `completed` | Current turn is idle and has new output | Review messages and diff |
| `queued` | A follow-up is waiting for the next boundary | Continue waiting |
| `needs_attention` | Permission or question is pending | Inspect and reply within scope |
| `retry` | Provider is retrying | Wait unless the error is persistent |
| `interrupted` | Current turn was aborted | Send a correction to resume if needed |
| `timed_out` | Wait window ended without a terminal event | Call wait again with the same cursor |

## Workspace modes

`worktree` is the safe default and starts from the repository's current `HEAD`. It does not include uncommitted or untracked files. Review the returned worktree and apply the approved patch deliberately.

Instruction discovery and hashing happen after the worktree is created. Consequently, the manifest represents the committed rule versions visible to DeepSeek. If a required rule exists only in the main working tree, spawning fails with a targeted message instead of silently omitting it.

`current` operates directly in the supplied directory. Use it only when the task depends on working-tree changes or isolation is impractical. Check `git status` first and protect unrelated edits.

## Review expectations

Review along the real call chain and test the failure boundaries most likely to be missed. Check that the patch stays inside scope, preserves user changes, handles errors honestly, and does not weaken permissions. Treat model-authentication failures, provider retries, and incomplete tests as failures rather than successful completion.
