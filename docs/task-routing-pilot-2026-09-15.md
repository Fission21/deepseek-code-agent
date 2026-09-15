# Task Routing Pilot — 2026-09-15

A transparent single-pair pilot of the task routing policy. It records one direct Codex execution and one delegated DeepSeek worker execution for each of a small, medium and large task, all from the same baseline commit `90d84be225fe2751ed0583917e7391ff29245559`. The machine-readable source is [task-routing-pilot-2026-09-15.json](./task-routing-pilot-2026-09-15.json); this page summarizes it and adds no new measurements.

This is not a formal benchmark. There is exactly one execution per cell, both arms ran in parallel, and the order was not alternated. The queen protocol asks for at least three alternated repetitions per variant, so this pilot does not satisfy it. Do not generalize these numbers, promise a fixed saving, or merge them with the [2026-09-14 combined-token report](./token-routing-benchmark-2026-09-14.md). Combined tokens and latency can still increase.

## Tasks

| Size | Task ID | Definition | Scope |
|---|---|---|---|
| small | notice-export-content-disposition-ascii-fallback | Content-Disposition fallback ASCII whitelist hardening | notice_export_service, service test |
| medium | notification-priority-dirty-input-degradation | Notification priority dirty-input stable degradation and enrich pure-function extraction | service, new test |
| large | export-lease-thread-safe-success-cooldown | Thread-safe export lease with success-commit cooldown, wired into three routes and covering failure rollback | service, 2 routes, tests |

The JSON records these IDs and definitions under `tasks`, matched to the three sizes.

## Raw results

### Direct Codex

| Size | Input | Cached input | Output | Reasoning | Total | Acceptance | Corrections |
|---|---:|---:|---:|---:|---:|---|---:|
| small | 554251 | 508032 | 3508 | 1034 | 557759 | true | 0 |
| medium | 812011 | 751872 | 7019 | 2209 | 819030 | true | 0 |
| large | 1696112 | 1626240 | 15444 | 4958 | 1711556 | true | 0 |

Direct total = input + output, where input includes cached input. These are model usage counters, not a billing statement.

### Delegated DeepSeek worker

| Size | Input | Cache read | Output | Reasoning | Total | Cost (USD) | Acceptance | Corrections | Usage complete |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| small | 26939 | 333952 | 3475 | 1842 | 366208 | 0.008232906 | true | 0 | true |
| medium | 27079 | 554240 | 8597 | 3316 | 593232 | 0.01287237 | true | 0 | false |
| large | 84304 | 5069568 | 23944 | 18216 | 5196032 | 0.053150304 | true | 1 | false |

Worker total = input + cache_read + output + reasoning. Medium and large are marked `usage_complete=false`; their totals and costs may understate actual usage. The provider-reported cost is retained as raw data and is not converted into a saving claim.

### Codex controller interval

From launching the six executions through accepting both arms, the controller increment was: input 6358712, cached input 6287488, output 18933, reasoning 6389, total 6377645, non-cached input 71224, proxy 90157.

This interval covers the whole two-arm experiment, including managing the native control arm and reviewing both arms. It is not a pure delegation bill and must not be presented as one.

### Wall time and independent acceptance

| Size | Direct wall time (s) | Delegated wall time (s) | Direct acceptance | Delegated acceptance |
|---|---:|---:|---|---|
| small | 108.921 | 210.517 | 32 tests | 41 tests |
| medium | 178.325 | 414.657 | 51 tests | 120 tests |
| large | 379.631 | 811.445 | 21 service + 49 API tests | 25 service + 53 API tests |

Wall time sources are recorded per execution in the JSON: direct durations span the Codex session's first-to-last timestamp; delegated durations run from worker creation through controller verification. The delegated large duration includes its one correction round. Independent controller verification passed for all six executions, with ruff, format and diff checks passing for each. Wall time values are single observations from arms that ran in parallel, so they can reflect contention for the same machine and provider capacity; they are not a stable performance baseline and are never used in token reduction calculations.

## Derived comparisons

Definitions: direct proxy = (input − cached input) + output; worker proxy = input + output; reduction = 1 − compared proxy / direct proxy total.

| Quantity | Value |
|---|---:|
| Direct proxy total (three tasks) | 202201 |
| Worker proxy total (three tasks) | 174338 |
| Worker proxy reduction vs direct proxy total | 13.78% |
| Controller proxy | 90157 |
| Controller proxy reduction vs direct proxy total | 55.41% |
| Direct total sum | 3088345 |
| Worker total sum (cache included) | 6155472 |

The 55.41% is a conservative controller-side comparison inside this pilot: the controller interval also absorbs native control-arm management and review of both arms, so it is neither a pure delegation bill nor a promised saving. Worker proxy was 13.78% below the direct proxy total, but worker total including cached reads was 6155472 against 3088345 direct, so combined tokens can increase. No fixed saving is claimed, here or elsewhere.

## Corrections and failures

All six executions were accepted. The three direct executions each needed 0 corrections, and the delegated small and medium executions were accepted with 0 corrections. The delegated large execution was rejected on first review: the first revision let a stale lease break a new lease and let empty results enter cooldown. The worker corrected both defects, and the revised patch passed review and independent verification. Nothing was discarded, and `usage_complete=false` for medium and large is retained as reported.

## Limitations

- One execution per size and arm, parallel, non-alternating. This is not the repeated formal benchmark required by the queen protocol and supports no statistical claim.
- One repository, one task set, one model configuration and one date. Nothing here generalizes, and no billing-grade conclusion follows.
- The controller interval includes native control-arm management and both-arm review, so 55.41% must not be treated as pure delegated savings.
- Worker usage completeness is false for two of three tasks, so the worker-side comparison may understate worker cost.
- Cache-inclusive worker total exceeded direct total, so combined tokens and latency may increase even when the worker proxy looks lower.
- Both arms ran in parallel, so each wall time can include contention for the same machine and provider capacity; the durations are single observations, not a stable performance baseline, and they feed no token reduction calculation.
- This pilot is independent of the earlier combined-token benchmark, and the two must not be mixed.
