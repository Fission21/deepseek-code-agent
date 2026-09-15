# Queen token efficiency

This workflow preserves queen (Codex) judgment and delegates only when there is a clear expected benefit from moving bounded labor out of the queen's workload. The queen owns interpreting the user's request, consequential decisions, uncertain diagnoses, risk-focused review and final acceptance. For justified delegation, read-only discovery, indexing and document gathering may happen first; before code changes, the queen checks the key contracts and design invariants that bound the implementation. The worker can consume more tokens to do that labor. This does not require lower combined token use.

The goal is not a queen that rubber-stamps a short report. Each worker handoff returns a scoped/versioned evidence packet: conclusions with source/test locations, short supporting excerpts, and explicit unexamined or uncertain items. The queen independently checks risk-critical paths and gaps without repeating all exploration. Ordinary reading and implementation need no step-by-step report; meaningful blockers are escalated and ordinary issues are batched.

## Routing after the read-heavy retest

Keep small known edits and tightly coupled reading or diagnosis with the queen by default. Consider delegation for repetitive implementation or independent evidence batches only when the expected queen labor removed outweighs dispatch, review and likely corrections within the time constraints. If benefit is unclear, work directly. This is a task-structure judgment, not a requirement to run a benchmark or seek approval before every dispatch. An explicit delegation request still takes precedence; a worker-model preference alone does not require spawning one.

The read-heavy pilot and retest do not justify making all bounded investigations prefer Luna. Their observed token changes and caveats remain below. The later routing-only correction described here was not model-benchmarked; the retest measures the earlier skill hash recorded in its report, not this subsequent version.

## What changed

- Codex keeps requirement interpretation, consequential design choices and acceptance. When delegation is justified, a bounded read-only scout may go first; before any code-changing delegation, Codex checks the key contracts and design invariants, then gives the worker a bounded objective and acceptance cases. A complete call-chain read is not required before every read-only exploration.
- Default wait/inspect responses omit task echoes, progress/tool logs and repeated instruction manifests. A bounded final report and worker usage remain available; full diagnostics are explicit.
- Preserve attention and failure signals. Revision-aware cursors must not lose a final result when a streamed message completes under the same message ID.
- The worker self-tests and fixes before handoff. `ds_verify_agent` separately runs the preselected checks, preserving full logs and source-bound evidence; inspection never executes checks. The queen reviews the actual diff and current evidence, then batches corrections to the same session. After two unsuccessful correction rounds, stop and confirm the worker and verification have exited before takeover.
- `return_on="actionable"` absorbs transient retries and escalates a continuous 120-second streak. Use host-supported bounded waits: the 55-second wait and 65-second host timeout remain, and a timeout may require another bounded wait. Do not promise zero polling, restart-proof completion or background wakeups.
- The original implementation update was functionally tested locally without a model comparison at that time. Later dated pilots or retests are separate evidence; historical measurements below describe their original versions and must not be treated as current-version results.

## Noise reduction is not proven token savings

OpenCode's compact payloads suppress task echoes, intermediate logs and repeated manifests, while preserving attention signals, cursor-aware final delivery and explicit full diagnostics. This is implemented transport filtering, not a guarantee of zero noise or improved queen reasoning quality. Native Luna does not use this bridge, so the bridge's measured payload reduction cannot be assigned to Luna.

Selective reading, batched output and concise reporting also apply to direct queen work. Fewer coordination calls in one retest do not prove that repeated reading or waits have been eliminated. Single-pair model variation, caching and unequal final-report length remain unresolved experimental confounders; do not turn a smaller payload or a small observed token reduction into a stable-savings claim.

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
| controller input/output/cached input | Actual host usage; retain the source and its token semantics |
| controller total | Primary optimization target: `input + output`, with cached input counted once |
| uncached input + output proxy | Secondary proxy only; do not present it as the primary controller total |
| worker input/output/reasoning/cache/total | Worker usage as reported by the lane; null means unavailable and it need not be lower |
| queen turns, tool calls, review/correction turns | Coordination and rework overhead |
| acceptance result | Independent tests plus review; failure is not savings |
| wall time | Include waits and retries |
| bridge response characters/bytes | Transport metric, separate from tokens |

Compute the primary controller total once from `input + output` under the host's token semantics. If reported input already includes cached input, never add cached input again. If the host reports cached and uncached fields separately, normalize them once to the same inclusive total and retain the raw counts. Uncached input plus output remains a separately labelled proxy. Do not sum per-message usage and its duplicate step-finish records. Unknown/partial values must not become zero.

For an accepted task, include all its failed attempts and correction turns in the operational cost:

`controller_saved_fraction = 1 - delegated_controller_tokens / direct_controller_tokens`

Report the median of matched controller-token savings and the range, plus first-pass acceptance rate, total acceptance rate and latency. Do not divide independently aggregated medians from different task mixes. An unresolved failure is a failed comparison, not a cheap success. Proposed release criterion: positive controller-token savings across representative non-trivial tasks without lower acceptance quality; the desired savings threshold is a product choice, not an existing measured result.

## Evidence status

The repository's earlier [combined-token report](token-routing-benchmark-2026-09-14.md) is retained unchanged. It cannot establish queen-only savings of this version. A compact payload benchmark or successful live bridge smoke test validates mechanics; neither alone proves end-to-end queen savings.

The [2026-09-15 read-heavy retest](reading-delegation-retest-2026-09-15.md) recorded 1,161,137 direct queen tokens versus 1,100,195 delegated queen tokens: **5.25% fewer** in one fresh matched pair, with **62.01% longer** elapsed time. Both reports covered the 12 core topics, but each omitted a checklist detail (11.5/12); report length also differed. This is an exploratory result, not proof of reliable savings, strict quality parity, implementation performance or total-worker-plus-queen savings. The earlier read-heavy pilot's 21.33% increase is retained in the dated comparison. Repeated matched runs remain necessary.


## Observed bridge results (2026-09-14)

Replaying the actual DS session that implemented and corrected this change (71 assistant messages) through the retained legacy envelope and new compact view produced a completed response of **26,688 → 3,301 characters (87.63% smaller)**. The final report was not truncated. Full diagnostic recovery and suppression of unchanged final reports were checked. See [machine-readable results](queen-payload-results.json).

This is one completed snapshot from a real worker transcript, compared locally against the old 20-message projection. It is not a live direct-versus-delegated queen experiment or proof of 87.63% queen token savings. No real transcript contents are committed. The deterministic 12-step fixture separately produced 19,165 → 1,068 characters (94.43% smaller).

Validation: 29 offline checks passed, one provider-prerequisite test was skipped in the offline suite, and a separate live DeepSeek smoke test passed through the default compact interface with instruction acknowledgements. These establish bridge behavior, not end-to-end quality parity or queen token savings.
