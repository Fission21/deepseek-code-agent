---
name: deepseek-coding-delegation
description: Keep the queen (Codex) on quality-critical engineering decisions while a DeepSeek or GLM worker handles bounded discovery, implementation, tests, routine fixes and evidence preparation. Use for coding tasks, coding goals, supporting engineering work, and selecting, querying or resetting this plugin’s worker model defaults; handle tiny known edits directly.
---

# DeepSeek Coding Delegation

Use the queen where its judgment most affects quality: intent, acceptance criteria, architecture and consequential tradeoffs, uncertain failure diagnosis, risk-focused review and final acceptance. The selected model is the worker: own high-volume investigation, implementation and evidence preparation within the agreed bounds. The objective is to preserve the queen’s capability while offloading labor, not to minimize queen involvement at the expense of quality. Combined tokens and latency can increase; do not promise measured savings without a comparable baseline.

## Divide judgment from labor

- Queen: decide what is correct, which constraints matter, consequential design choices and whether the evidence supports acceptance. Spend attention on unresolved uncertainty and risky changes.
- Worker: search files, trace calls, inventory dependencies, compare existing patterns, edit code and docs, add/run relevant tests, fix known failures, reduce logs and prepare a diff/evidence summary. Delegate these supporting tasks even when their output is investigation rather than a patch.
- For a blocking design/business choice, ask the worker for the decision needed, options, tradeoffs and its recommendation, with source paths or test evidence. The queen decides; the worker resumes. Do not interrupt the queen for ordinary implementation choices already covered by scope.

## Select the worker model

- Accept ordinary language. “OpenCode Go / Go 套餐” selects `opencode-go`; “GLM 5.3 Flash” selects `glm-5.3-flash`; “官方 DeepSeek / DeepSeek API” selects `deepseek`, with Flash model `deepseek-flash`. Preserve other explicitly named model IDs and validate the catalog.
- Map “推理最大 / 最高 / 拉满 / max” to `variant="max"`, “高 / high” to `high`, “低 / low” to `low`, and “默认档位 / 自动” to `null`. `high` does not satisfy a request for maximum reasoning. Never silently lower an unsupported variant.
- The chosen interaction convention is that a standalone model/effort-setting request, such as “使用 OpenCode Go 里面的 GLM 5.3 Flash，推理开到最大”, saves the machine default through `ds_model_defaults(action="set", ...)`. “以后 / 全局 / 所有新执行者” also means persistent. Preserve scope the user already established. “这次 / 当前任务” or a model choice attached to a specific coding task overrides that task's new workers only: pass the full selection on their spawns without saving global defaults.
- After setting a default, report the resolved provider, model, variant and scope briefly. It covers this plugin's subsequently created workers on this machine, including future Codex tasks; existing workers keep their selection. It does not change the Codex controller model. Use `action="get"` for “查看当前默认模型” and `action="reset"` for “恢复插件原来的默认模型”.
- With no explicit task selection, let check/spawn inherit the saved machine default rather than passing hardcoded model arguments. With no saved preference, the built-in default remains `opencode-go/deepseek-v4.1-flash` with `max`. Call `ds_check` with the same overrides and workspace before first use of that configuration; its `ok` means configured and listed, not verified credentials or inference.
- Follow-ups, queued messages and forks preserve their own stored selection, including after a global-default change. Start a new worker when the task explicitly changes model. Never silently switch provider or model after a failure or quota limit.
- Both providers use OpenCode as the agent runtime; the official provider uses separate DeepSeek API credentials. Read [model selection and setup](references/model-selection.md) for precedence, authentication, CLI examples or missing models. Keep keys out of prompts and manifests.

## Route and delegate

- Handle a tiny, understood edit directly when describing it and reviewing it would take more work than doing it. Hand off a coherent behavior or investigation with acceptance criteria, rather than one function or tool command per turn.
- Prefer one persistent worker. Avoid additional queen-model workers for ordinary implementation. Reuse the worker for related corrections; use a fresh session for unrelated tasks.
- Before spawning, read applicable repository instructions and the critical call chain. Codex must decide the interface, data flow, invariants, failure behavior and acceptance examples that determine correctness. Leave routine implementation and broader discovery to the worker. Do not substitute a vague task restatement for a design, or prewrite every implementation detail.
- Send a `task_spec` with the design decisions, observable acceptance criteria and exact checks selected by Codex. Keep it grounded in existing contracts; do not add speculative constraints. For an unfamiliar subsystem, first request a bounded discovery report from the same worker, then resolve the consequential decisions before implementation.
- Use concrete `scope_paths`, task-relevant `required_reads` (named sections for large files), and a few non-secret `critical_constraints`. Send paths and observable requirements, not full source files or conversation history.
- Prefer `workspace_mode="worktree"` for work starting from HEAD. Use `current` when the worker needs uncommitted changes, after checking status and protecting unrelated edits. Explain that it edits the shared tree.

## Keep the queen loop small

1. Check prerequisites once per environment and selected model configuration. Spawn with the objective, allowed modules, instruction routing and `task_spec`. Ask the worker to implement, self-test and fix ordinary failures continuously before returning one concise handoff. It must escalate a contradictory design, missing consequential decision or out-of-scope requirement rather than guessing.
2. Keep the agent ID, worktree, cursor and instruction manifest. Use `ds_wait_agent(return_on="actionable", timeout_ms=55000)` with the latest cursor. Ordinary timeouts require another bounded wait, not a new diagnosis or transcript replay. Handle real attention events within existing authorization. The host still has a 65-second tool timeout: this workflow does not promise background completion notifications or zero polling.
3. Once the worker is idle, call `ds_verify_agent` to run only the checks preselected by Codex. Poll the same verification job when it is running; do not request `rerun` merely to read its result. Inspect does not execute checks. Read the current patch-bound evidence and actual diff, compare instruction acknowledgements, and examine relevant surrounding code. Actual check success is evidence, not Codex acceptance; stale, unavailable, failed, timed-out or cancelled verification is not a pass.
4. Review the whole scoped change before sending one batch of concrete issues, evidence and expected behavior. Use the same worker for at most two review correction rounds. Worker self-tests and routine compile/format fixes should not each cause a queen round trip. Never weaken an acceptance criterion just to finish a round.
5. If two review correction rounds still fail, Codex takes over the remaining repair. Interrupt the worker, cancel any verification and confirm it has stopped before editing the shared files. Do not infer stopped status from an abort acknowledgement alone. Preserve its patch and include all takeover work in the delegated route's accounting. Do not silently change the selected worker model.
6. Preserve and apply the reviewed patch, then close the agent. Remove its worktree only after useful changes are preserved. Codex owns `/goal` completion after review and validation.

Use `ds_inspect_agent` with `detail="full"` only for missing/truncated evidence, errors, instruction acknowledgements or a specific diagnosis; `include_diff=true` explicitly requests the diff. Never echo large logs into queen messages just to summarize them again. Do not infer success from idle, a timeout or unavailable usage.

The worker must not commit, push, merge, deploy, modify production data, expose secrets or discard unrelated edits without the user's authorization for that action. If delegation is unavailable, report the limitation and continue locally when authorized.

## Measure the intended outcome

Optimize high-quality-model consumption while preserving design and review quality. Report Codex input, cached input, output and total separately from worker usage. When measuring, distinguish planning, coordination, review and takeover phases; include failed attempts and all correction rounds. Codex noncached input plus output is a useful proxy, not a subscription-quota or billing conversion. Keep model/effort unchanged when testing the workflow. Tiny tasks may route directly and must be reported as direct.

Benchmark or live inference runs require the user's task to include them. Do not start extra comparisons merely to validate a Skill update. Local mocked/unit checks are sufficient to report implementation correctness; they do not prove token savings.

Read [control contract](references/control-contract.md) when instruction routing, cursor recovery, workspace modes or a lifecycle edge case needs clarification. It is not required reading on every ordinary delegation.
