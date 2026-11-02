# DSH reliability upgrade (approved)

DSH remains explicitly selected. Codex leads; workers implement, repair and provide short handoffs. No extra human confirmation or duplicate blanket test runs.

## Contract

1. Each task receives acceptance criteria and optionally parent-selected argv test commands. The runner executes those commands once after implementation, records exit status and before/after source fingerprints. Integrators receive the appropriate integration/full-suite command. Execution completion is separate from the parent's final diff review.
2. Two failed executions by default trip a circuit; total execution time is bounded across continuations. A parent may explicitly acknowledge and revise the budget. Timeouts and uncertain writes are never automatically replayed.
3. Worker liveness and meaningful progress are separate. A watchdog monitors the scheduler, restarts supervision after death/stall, and the scheduler stops stuck workers. Pending parent questions have their own existing deadline.
4. Peer events are question/answer/blocked/artifact_ready, bounded and correlated. Optional immutable group interface contract is injected into every task. Handoffs carry summary, rationale, checks and open issues.
5. SQLite uses WAL, bounded busy waiting and revision comparisons on parent revisions. Short metadata transactions remain multi-process; this is not a single-process database rewrite.
6. A browser MCP proxy exposes a constrained tool set for the assigned tab and origin. Default calls are read-only; writes need an exact, single-use, expiring grant from Codex. Same-user shell access is not an OS security sandbox. Do not imply the proxy can prevent shell bypass or navigation races inside Chrome.
7. A global hard cap and per-team occupancy caps use work-conserving round-robin dispatch. No preemption and no permanently reserved idle slot.

## Implementation sequence and checks

- Scheduler metadata: acceptance/limits, handoffs, circuit, review revisions, peer limits, fairness. Test competing teams, stale revisions, failed retries and bounded peer messages.
- Runner and watchdog: execute recorded acceptance commands, track heartbeat/progress and recover scheduler supervision. Fake DSH/process tests cover failed checks, changed source, hangs and scheduler death without replay.
- Browser proxy: exact tab/origin and single-use grants. Fake upstream JSON-RPC tests prove denied actions never reach the browser and grants expire.
- Surface and delivery: MCP schema, skill/reference documentation, portable local install; full Python/Node regression and one independent final diff review. No real model calls or live browser writes.

## Shared interfaces

- Scheduler writes `team.json`; runner and controller consume acceptance, limits and contract. Additive fields keep existing sessions readable.
- Runner reports `acceptance`; scheduler uses it before releasing dependencies. Parent review records revisions plus delivered artifacts, invalidated on resume.
- Browser proxy writes requests to the mailbox; parent API creates exact grants, consumed before forwarding to upstream.

## Review focus

Cancellation races, stale invocation/revision, leaked descendants, secret-bearing tool bodies, malformed JSON-RPC, cross-tab or changed-origin access, starvation and continuation budget bypass. This is a local cooperative worker system, not isolation from a malicious same-user process.

## Validation and delivery boundary

- Parent delivery is a judgment from existing evidence and proportionate review of important changes. Failed work returns to its owner; no blanket second test pass or automatic parent implementation takeover.
- Offline regression: 90 Python cases (89 passed, one platform skip) and 129 Node cases (128 passed, one skip). Includes actual fake-worker processes, scheduler death/freeze recovery, no replay, source-bound acceptance, bounded peer delivery and an upstream MCP subprocess.
- The old overlap test assumed the oldest team always filled both slots; it now checks actual overlap and the global cap while allowing fair cross-team ordering.
- Real provider/model and live Chrome operations were not used in this upgrade.

## Final independent review

Four important findings were reproduced before fixing: browser input/selector values in request records, unhandled source-fingerprint errors, malformed browser JSON-RPC, and blocked outcomes incorrectly counted as failed acceptance. Each regression now passes. Final local suite: 95 Python cases (94 passed, one platform skip); 129 Node cases (128 passed, one skip).

Scope decisions retained: this gate does not isolate a malicious process running as the same OS user; that requires OS permissions/sandboxing. Pre/post origin checks do not make navigation or action batches atomic; a browser race remains possible, so no stronger permission-isolation guarantee is claimed. No minor findings were deferred.
