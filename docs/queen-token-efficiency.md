# Queen token efficiency

This version preserves queen (Codex) judgment while moving high-volume work to DS. The queen owns intent, consequential design choices, uncertain diagnoses, risk-focused review and acceptance. DS handles code discovery, implementation, tests, routine fixes, documentation, log reduction and evidence preparation. DS can consume more tokens to do that labor. This does not claim lower combined token use.

The goal is not a queen that rubber-stamps a short report. The goal is a queen that spends its capacity where it changes quality. A blocking consequential decision should return a short packet of options, tradeoffs, recommendation and evidence; ordinary implementation decisions stay with the worker.

## What changed

- Codex reads the critical call chain and decides interfaces, data flow, invariants and acceptance cases before dispatch. Delegate a complete implementation with those decisions in `task_spec`; the worker handles the remaining local details.
- Default wait/inspect responses omit task echoes, progress/tool logs and repeated instruction manifests. A bounded final report and worker usage remain available; full diagnostics are explicit.
- Preserve attention and failure signals. Revision-aware cursors must not lose a final result when a streamed message completes under the same message ID.
- The worker self-tests and fixes before handoff. `ds_verify_agent` separately runs the preselected checks, preserving full logs and source-bound evidence; inspection never executes checks. The queen reviews the actual diff and current evidence, then batches corrections to the same session. After two unsuccessful correction rounds, stop and confirm the worker and verification have exited before takeover.
- `return_on="actionable"` absorbs transient retries and escalates a continuous 120-second streak. The 55-second wait and 65-second host timeout remain; this version does not provide proactive completion wakeups.
- This update was functionally tested locally. No new model comparison was run; the token savings and latency goals remain unmeasured. Historical measurements below describe their original versions and must not be treated as current-version results.

## Two different measurements

**Transport measurement:** replay the same worker transcript through full and compact snapshots. Compare serialized response characters/UTF-8 bytes, including repeated waits and the final response. Check that final evidence, failure and attention signals survive. This is deterministic and isolates the bridge, but it does not measure model tokens or the queen's behavior. No model calls are needed for synthetic fixtures.

Run the offline transport fixture with:

```bash
node plugins/deepseek-code-agent/scripts/benchmark-payload.mjs
```

An optional first argument accepts a local JSON array of raw OpenCode messages from an authorized completed session. It prints only measurements, not transcript contents. Keep real transcripts outside the repository; they may contain source code or user data. The legacy comparison retains the old 20-message envelope. A supplied transcript measures one completed snapshot plus an unchanged replay, not a full live timeline.

**End-to-end measurement:** run a direct queen baseline and a queen + DS variant on the same coding task from the same pinned commit. Use fresh queen tasks, identical queen model/reasoning settings and the same hidden acceptance checks. Retain worker context only for corrections inside a single attempt, not across variants. Alternate run order and repeat each variant at least three times for each representative task. Include a known small edit, unfamiliar multi-file behavior and a bug with a regression test.

Record for every attempt:

| Field | Meaning |
| --- | --- |
| task, commit, variant, attempt | Matched identity, no discarded failures |
| queen input/output/cached input | Actual provider or host usage; retain the source and its token semantics |
| DS input/output/reasoning/cache/total | Worker usage as reported by OpenCode; null means unavailable |
| queen turns, tool calls, review/correction turns | Coordination and rework overhead |
| acceptance result | Independent tests plus review; failure is not savings |
| wall time | Include waits and retries |
| bridge response characters/bytes | Transport metric, separate from tokens |

Compute queen total from input + output only when the reporting API defines input as inclusive of cached input. Never add cached input again under that convention. If semantics are different, normalize explicitly and retain raw counts. Do not sum per-message usage and its duplicate step-finish records. Unknown/partial values must not become zero.

For an accepted task, include all its failed attempts and correction turns in the operational cost:

`queen_saved_fraction = 1 - delegated_queen_tokens / direct_queen_tokens`

Report the median of matched savings and the range, plus first-pass acceptance rate, total acceptance rate and latency. Do not divide independently aggregated medians from different task mixes. An unresolved failure is a failed comparison, not a cheap success. Proposed release criterion: positive queen savings across representative non-trivial tasks without lower acceptance quality; the desired savings threshold is a product choice, not an existing measured result.

## Evidence status

The repository's earlier [combined-token report](token-routing-benchmark-2026-09-14.md) is retained unchanged. It cannot establish queen-only savings of this version. A compact payload benchmark or successful live bridge smoke test validates mechanics; neither alone proves end-to-end queen savings. Until matched queen usage is collected, the size of real queen savings remains unverified.


## Observed bridge results (2026-09-14)

Replaying the actual DS session that implemented and corrected this change (71 assistant messages) through the retained legacy envelope and new compact view produced a completed response of **26,688 → 3,301 characters (87.63% smaller)**. The final report was not truncated. Full diagnostic recovery and suppression of unchanged final reports were checked. See [machine-readable results](queen-payload-results.json).

This is one completed snapshot from a real worker transcript, compared locally against the old 20-message projection. It is not a live direct-versus-delegated queen experiment or proof of 87.63% queen token savings. No real transcript contents are committed. The deterministic 12-step fixture separately produced 19,165 → 1,068 characters (94.43% smaller).

Validation: 29 offline checks passed, one provider-prerequisite test was skipped in the offline suite, and a separate live DeepSeek smoke test passed through the default compact interface with instruction acknowledgements. These establish bridge behavior, not end-to-end quality parity or queen token savings.
