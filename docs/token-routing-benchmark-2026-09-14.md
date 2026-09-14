# Token Routing Benchmark — 2026-09-14

This report measures whether routing Codex implementation work through DeepSeek reduces total model tokens. It is a project-maintainer benchmark, not an independent evaluation or a universal model ranking.

## Setup

- Nine synthetic coding tasks: three small, three medium, and three large.
- Three fresh-task variants per task: `direct`, optimized `policy`, and `forced` DeepSeek delegation.
- The same Codex model and reasoning effort for every run.
- Every task started from the same pinned Git commit in a disposable isolated worktree.
- No database, external business service, Docker service, browser, worker, or scheduler was used.
- A run passed only when its complete prescribed validation sequence passed. Failed attempts were recorded separately and never counted as token savings.
- `route_total = codex_total + deepseek_total`. Cached tokens remain part of the processed-token total and are also recorded separately.

After retrying failed attempts in fresh tasks, all 27 task/variant slots had a successful comparable result. There were 38 attempts in total: 27 successful attempts and 11 failures.

## Median successful result by task size

| Size | Direct route tokens | Policy route tokens | Policy savings vs direct | Forced route tokens | Forced savings vs direct |
|---|---:|---:|---:|---:|---:|
| Small | 329,752 | 735,032 | -122.90% | 1,758,139 | -433.17% |
| Medium | 509,666 | 2,634,784 | -416.96% | 3,900,068 | -665.22% |
| Large | 1,058,360 | 5,549,401 | -424.34% | 3,695,768 | -249.20% |

A negative saving means the route used more tokens than direct execution. Expressed as multiples, policy used approximately 2.23x, 5.17x, and 5.24x the direct route tokens for small, medium, and large tasks.

## Median wall time

| Size | Direct | Policy | Forced |
|---|---:|---:|---:|
| Small | 115.7 s | 188.9 s | 335.7 s |
| Medium | 137.0 s | 465.8 s | 524.4 s |
| Large | 206.6 s | 549.9 s | 443.2 s |

Raw attempt success rates, including failed attempts, were 64.29% for small, 90.00% for medium, and 64.29% for large tasks. Failures included environment-harness issues, validation failures, and worker/controller rework; they are part of the operational overhead rather than savings.

## Interpretation

- Direct execution was the lowest-token and lowest-latency route in every size bucket.
- Policy routing improved on forced delegation for small and medium tasks by 58.19% and 32.44% respectively, but used 50.16% more route tokens than forced delegation for large tasks.
- Policy therefore reduces unnecessary delegation relative to `forced` in some workloads, but it did not make delegation cheaper than direct execution in this benchmark.
- Use delegation for the value of an independent implementation pass, bounded repository discovery, or a second engineering perspective. Do not advertise or select it primarily as a token-saving feature.

## Limitations

The sample contains nine synthetic tasks from one repository and one fixed tool/model configuration. Results can change with task shape, prompts, models, caching, controller behavior, and provider pricing. Future claims should be based on repeated benchmarks across more repositories and should continue to report failures and retries rather than filtering them out.
