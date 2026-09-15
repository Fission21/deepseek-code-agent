---
name: deepseek-coding-delegation
description: Route coding and read-heavy engineering work between direct Codex execution and bounded Luna/DeepSeek/GLM workers based on expected Queen-token benefit. Use for delegation decisions and worker model/default settings; keep tightly coupled investigation and tiny known edits direct unless the user requests delegation.
---

# DeepSeek Coding Delegation

Reduce **queen (Codex) model tokens**, not its responsibility. The queen interprets the user's needs, decides consequential tradeoffs and accepts the result. Workers can spend more tokens on discovery, repetitive reading, implementation, tests, ordinary repairs and evidence preparation. Neither total-system savings nor quality follows automatically from delegation.

## Choose the work boundary

- Honor an explicit direct/delegated choice. Otherwise keep tiny known edits and tightly coupled reading or diagnosis with the queen, especially when it would need to repeat most of the worker's investigation.
- Before automatic delegation, identify the separable labor it removes from the queen and weigh that against dispatch, evidence review and likely correction work, while respecting time constraints. Large reading volume, a cheaper worker or available parallel slots alone do not establish a benefit. If the expected benefit is unclear, work directly; this judgment does not require a new benchmark or approval step.
- Repetitive implementation or independent evidence batches with clear acceptance can justify delegation. Once delegation is justified, prefer native `gpt-5.6-luna` for bounded low-risk work when available, unless another model/lane was selected. Parallelize only genuinely independent work, not a separate worker per question or command; more workers do not automatically save queen tokens.
- Prefer DeepSeek/GLM through OpenCode when selected by the user or when cross-task/restart state discovery, OpenCode context forks, external-provider accounting or persisted `task_spec` verification materially helps. Task-scoped corrections and a Codex-managed worktree do not alone require OpenCode.
- For risky implementation (state machines, concurrency, permissions, transactions, migrations), the queen decides interfaces, invariants, failure semantics and acceptance before code changes. It may implement a difficult core itself and delegate surrounding labor; it is not limited to reviewing workers.

## When read-heavy delegation is justified

The queen first reads applicable instructions and turns the **user's request** into questions, scope and acceptance criteria. Use the routing decision above before dispatch: unfamiliarity or reading volume alone is not a reason to delegate. For justified delegation, do not require the queen to finish the same source investigation first; the worker may discover unfamiliar contracts, then the queen checks consequential evidence and resolves the design before authorizing implementation.

Give the worker one bounded assignment covering discovery and evidence preparation. Ask it to search before reading, batch related file ranges, reuse already-read material and distinguish implementation from documentation and tests actually run. Routine lookup choices do not need queen approval.

Request one decision-ready evidence packet:

- Scope and revision/workspace identity; relevant files actually examined and important exclusions.
- An answer per requested question, with exact source/test locations and short decisive excerpts where needed to make a claim checkable.
- Contradictions, missing evidence, uncertainty and coverage limits; distinguish a test definition from an executed result. “Not found in these files” is not “does not exist”.
- Only decisions that need the queen, with options and their consequences. Facts and proposed decisions must remain distinguishable.

Return the packet once, not a stream of reading notes. Save a longer artifact when useful, but do not replay it into multiple messages. Never omit a failure, consequential uncertainty or necessary evidence to meet a length target.

The queen reads the packet, then **independently checks authoritative sources for decision-changing and safety-critical claims**, plus gaps or contradictions. Batch related checks into a focused read when possible. Do not repeat the worker's entire search, reread unchanged rules or reproduce every log merely to summarize it again. Expand review when evidence is weak or risk requires it; a token target never overrides acceptance. For code changes, review the actual scoped diff and run independently selected checks, not just the packet.

## Keep coordination small

Selective reading, batched tool output and concise reporting also apply when the queen works directly; these noise reductions do not require a worker.

- Send the known scope, constraints and expected output together at dispatch. Do not add reminders while the worker is already doing the requested work; batch genuine corrections instead.
- Leave ordinary test failures, formatting and local implementation repairs with the worker. Escalate a consequential choice, contradictory requirement, permission boundary or real blocker, not routine progress.
- While waiting, do useful **non-duplicating** work such as defining acceptance from the requirements. Otherwise use the host's bounded wait/event mechanism; avoid alternating wait, list and inspect on unchanged state. A timeout is not failure and not a reason to replay context. Respect host wait limits and required user updates; do not claim zero polling or unsupported background recovery.
- Use compact completion/evidence responses. Read full diagnostics only for a specific missing fact or failure. Combine independent reads/checks and expose only the relevant output; retain complete logs outside the main context when needed. This reduces transport and round trips, not the verification standard.
- Reuse one worker for related corrections. After two unsuccessful review correction rounds, stop it and any verification, confirm they have actually stopped, then let the queen take over. Count all attempts and takeover work; do not silently change models.

## Models and native Luna

- An explicit per-task or fleet instruction (for example “当前任务所有工蜂都用 5.6 Luna”) overrides automatic routing for every new delegate in that scope. Existing workers retain their creation-time selection; replace them deliberately to change models, preserving useful work. Unless the user says future/global, the scope is this task/thread. Selecting a worker model does not itself require spawning a worker.
- Spawn native Luna with `model="gpt-5.6-luna"` and **`reasoning_effort="max"`** unless the user explicitly requests another effort. Explicit “默认/自动” means omit effort; an unspecified effort still means max. Prefer focused task context to a full conversation fork when the host permits it. Preserve the relevant instructions and constraints in that context.
- Use native spawn/message/follow-up/wait/interrupt capabilities and retain the agent ID for coherent task-scoped corrections. Never call `ds_*`, start OpenCode or pass external credentials for Luna. Native availability and effort support come from the active host; unsupported selections must be reported, not silently lowered or rerouted.
- Native model preferences cannot be persisted with `ds_model_defaults`. For external model/default requests, authentication, catalog errors or detailed selection precedence, read [model selection](references/model-selection.md). External defaults and task overrides remain distinct; existing sessions/forks keep their saved selection. Ordinary OpenCode dispatch inherits saved defaults, with Go DeepSeek/max as the fallback when none are saved.

## Workspace and OpenCode lifecycle

Use current files for read-only work or serial tasks that depend on uncommitted changes. For independent writes from committed state, prefer a host-managed worktree, or create one and give the worker its exact directory and write boundary. Worktrees do not contain uncommitted/untracked changes. A directory instruction is not a sandbox; protect unrelated work and avoid overlapping writes. Do not create a user-visible Codex task merely to emulate a hidden worker without an explicit request for a new task.

Before the first OpenCode dispatch, read the [control contract](references/control-contract.md) for instruction manifests, `task_spec`, queue/wait/verification mechanics and recovery. Reuse those instructions while unchanged. Preflight once for the selected configuration; `ds_check` verifies catalog/configuration, not credentials or inference. For planned implementation pass queen-selected design, acceptance and exact checks in `task_spec`; read-only discovery need not invent executable checks.

For OpenCode, use actionable waits with the latest cursor, verify while idle using the persisted checks, and inspect actual diff plus current patch-bound evidence. Inspect never runs checks; repeated verify observes the same job unless `rerun` is deliberately requested. Failed, stale, unavailable, timed-out or cancelled evidence cannot pass acceptance. Compare manifest acknowledgements, which identify versions but do not prove compliance. Native Luna needs the same independent acceptance, performed through available native/file/check tools rather than `ds_verify_agent`.

Preserve/apply reviewed changes before closing a worker or removing its exact worktree. Neither lane may commit, push, merge, deploy, modify production data, expose secrets or discard unrelated edits without the corresponding user authorization. If a lane is unavailable, report the limitation; continue locally only when authorized rather than silently selecting another worker lane.

## Measure the intended result

Report queen, Luna and external-worker usage separately. The primary token metric is **actual queen input + output**, normalizing whether input includes cache and never adding cached input or reasoning output twice. Keep uncached input + output as a separately labeled proxy, not the total, bill or Codex quota. A lower proxy alone is not success when queen total increased. Smaller payloads or one small measured reduction do not establish stable queen savings or improved reasoning quality; retain contrary results and unresolved experimental confounders.

Compare the same task, source, queen model/effort and acceptance, including rule reads, dispatch, waits, review, corrections and takeover. Record worker lane/effort separately and retain failed attempts. Tiny tasks routed directly must be labeled direct. Start model benchmarks only when the user's task includes them; local validation or smaller payloads alone do not prove queen-token savings.
