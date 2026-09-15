# DeepSeek control contract

Read this reference when instruction routing, cursor recovery or a lifecycle edge case needs clarification.

## Context capsule

Keep the first message concise and operational:

```text
Objective:
Known entry points and critical call chain:
Design decisions and compatibility invariants:
Allowed files or modules:
Acceptance criteria:
Required checks:
Do not:
Return:
```

Under `Return`, request a concise handoff with outcome, changed files, checks actually run, remaining risks and manifest acknowledgement. Assign implementation, tests and routine repairs together. Before implementation, Codex checks the critical contracts and decides interfaces, data flow, compatibility invariants and failure boundaries. Only after delegation is justified by the routing decision in SKILL.md, use bounded discovery with questions, scope and an evidence-packet requirement; unfamiliarity or reading volume alone does not require a worker. For such delegated discovery, do not require Codex to repeat the investigation before dispatch. The queen verifies consequential source evidence and decides the design before code changes. Omit implementation-only fields and executable checks for a purely read-only discovery task when they do not apply.

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

## Structured task and verification

For planned implementation, pass `task_spec` to `ds_spawn_agent`:

```json
{
  "version": 1,
  "design_decisions": ["Return a shallow copy; preserve nested object identity."],
  "acceptance_criteria": ["The input dictionary is unchanged and existing labels stay compatible."],
  "checks": [
    {"id": "regression", "argv": ["python", "-m", "pytest", "tests/test_example.py", "-q"], "cwd": ".", "timeout_ms": 60000}
  ]
}
```

Use actual native executable paths available on the host (for Windows Python, normally the existing virtual environment's `python.exe`). Checks use argument arrays without shell expansion; Windows `.cmd` and `.bat` shims are rejected. `cwd` is workspace-relative and must stay inside it after symlink resolution. Do not place secrets in commands or task specifications. Commands are selected by Codex within the user's authorized task; never turn worker-provided shell text into a verification command.

The specification is optional for compatibility. Without configured checks, verification cannot claim success. `ds_verify_agent({agent_id, wait_ms: 0})` starts or observes a verification job; bounded waits may be requested up to 55 seconds. Repeated calls return the same job. Use `rerun: true` only when deliberately requesting another check execution. Worker work and verification cannot run concurrently for the same agent.

Results retain actual exit codes and complete log paths outside the worker workspace. Compare the source snapshot and instruction/specification identity: edits during checks or after a pass invalidate it. Git snapshots cover HEAD, tracked changes and nonignored untracked content; nongit snapshots explicitly cover declared scope. Ignored inputs outside the declared snapshot are not independently attested. Passing process checks does not prove semantic instruction compliance or replace Codex review.

To repair a failed review, send a single batch of evidenced issues. After two unsuccessful review correction rounds, interrupt and confirm the worker/verification stopped before Codex edits the shared workspace. Preserve all attempts in measurement and keep the configured model unchanged.

## Tool lifecycle

- `ds_model_defaults`: get, set or reset the local machine default for subsequent workers. Change it only for a persistent model-setting request; existing agents keep their selection. See [model selection](model-selection.md) for natural-language scope and precedence.

- `ds_check`: check Node, OpenCode and the selected provider/model configuration; pass the same provider, model, variant and workspace as the planned spawn. This does not verify credentials, balance or inference. See [model selection](model-selection.md).
- `ds_spawn_agent`: create a persistent OpenCode session and immediately submit the initial task. Save `agent_id`, `cursor`, and `worktree` from the result.
- `ds_wait_agent`: use `return_on="actionable"`, `timeout_ms=55000` and the returned cursor for this workflow. Compact timeouts contain only the necessary state. Transient retries stay inside the bounded wait; a continuous 120-second retry streak escalates to `needs_attention`. Completion/failure/permissions/questions keep precedence. Omitted `return_on` retains legacy behavior. Treat cursors as opaque; retain them across timeouts.
- `ds_verify_agent`: execute only the persisted, Codex-selected checks while the worker is idle; observe the existing job on repeated calls, explicitly rerun when needed. Read actual exit codes, full log paths and snapshot validity before review. Inspect never starts verification.
- `ds_send_message`: continue the same context. When the agent is busy, the bridge queues the message and sends it at the next idle boundary.
- `ds_inspect_agent`: compact status and final handoff by default; completion/failure includes cumulative worker usage. Unchanged waits omit repeated usage and workspace metadata. Request `detail="full"` for recent messages and the instruction manifest; `include_diff=true` explicitly requests the diff. If a handoff is truncated or lacks a required acknowledgement, inspect full without the consumed cursor to retrieve that evidence. Full message text retains the bridge per-part truncation limit; inspect the actual files for larger evidence.
- `ds_fork_agent`: fork the conversation from its current or specified message. A fork shares the same filesystem, so do not run parent and child concurrently.
- `ds_reply_agent`: answer a permission or question request. Prefer one-time permission; reject requests outside the task scope.
- `ds_interrupt_agent`: stop the current turn while preserving the session.
- `ds_close_agent`: abort current work and mark the bridge record closed. Set `remove_worktree=true` only after preserving useful changes.
- `ds_list_agents`: recover local agent IDs after a Codex restart.

## State mapping

| Bridge state | Meaning | Controller action |
|---|---|---|
| `running` | The worker is working | Wait using the returned cursor |
| `completed` | Current turn ended with an assistant result | Review handoff and diff; verify acceptance |
| `failed` | Assistant/provider error | Inspect error and diagnose before retry |
| `queued` | A follow-up is waiting for the next boundary | Continue waiting |
| `needs_attention` | Permission or question is pending | Inspect and reply within scope |
| `retry` | Provider is retrying | Wait unless the error is persistent |
| `interrupted` | Current turn was aborted | Send a correction to resume if needed |
| `timed_out` | Wait window ended without a terminal event | Call wait again with the returned cursor |

## Workspace modes

`worktree` is the safe default and starts from the repository's current `HEAD`. It does not include uncommitted or untracked files. Review the returned worktree and apply the approved patch deliberately.

Instruction discovery and hashing happen after the worktree is created. Consequently, the manifest represents the committed rule versions visible to the worker. If a required rule exists only in the main working tree, spawning fails with a targeted message instead of silently omitting it.

`current` operates directly in the supplied directory. Use it only when the task depends on working-tree changes or isolation is impractical. Check `git status` first and protect unrelated edits.

## Review expectations

Review along the real call chain and test the failure boundaries most likely to be missed. Check that the patch stays inside scope, preserves user changes, handles errors honestly, and does not weaken permissions. Treat model-authentication failures, provider retries, and incomplete tests as failures rather than successful completion.
