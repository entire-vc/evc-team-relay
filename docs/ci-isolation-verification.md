# Database isolation and CI verification record

This record makes the verification evidence available in an ordinary checkout.
It records observed results for MR !341; results are snapshots, not promises
about future runs. The change is limited to tests, CI and its security checks.
No production deployment or release is part of this work.

## Regressions and positive controls

The tests use the normal SQLite engine, HTTP clients and application session
factory. Known-bad controls were run before accepting the corresponding fix.
The following are command-output summaries, with each mutation restored before
committing. Mutations exercise assertion behavior rather than lower thresholds.

| Check / known-bad input | Observed failing result | Fixed positive control |
|---|---|---|
| Isolation tests on schema-reset baseline | 2 failed, 1 passed | 5 passed |
| Omit physical SQLite BEGIN before SAVEPOINT | 1 failed | isolation regression passes |
| Remove raw DBAPI transaction-escape guard | 1 failed | raw commit escape regression passes |
| Substitute incorrect results in seven previously empty tests | 7 failed | 43 focused tests passed |
| Webhook returns empty result | 1 failed | focused tests passed |
| Omit current audit event, leave unrelated prior audit row | 1 failed | current event identity passes |
| Refresh uses stale issued-at value | 1 failed | controlled-clock refresh passes |
| Unsafe shared/unknown path rules | CI contracts fail | 14 CI contracts pass |
| Daily job uses unsafe worker/default controls | CI contracts fail | explicit n2 contract passes |
| Existing Semgrep XML parser finding | scan finding | fixed scan passes |
| source-map-js 1.2.1 HIGH vulnerability | Trivy exit1 | source-map-js1.2.2, Trivy exit0 |

Commands for the permanent controls:

```sh
cd apps/control-plane
uv run pytest -q tests/test_database_isolation.py
uv run pytest -q tests/test_refresh_token.py tests/test_billing.py
uv run pytest -q --junitxml=serial.xml --shuffle-seed=6608
uv run --with pytest-xdist==3.8.0 pytest -q -n 2 --dist load \
  --shuffle-seed=6608 --junitxml=parallel.xml
# From the repository root:
python3 scripts/test_ci_rules.py
```

Observed isolation result: `5 passed, 68 warnings in 0.90s`.
Focused correctness result: `43 passed, 410 warnings in 13.10s`.
Independent focused check: `24 passed, 143 warnings in 2.61s`;
additional independent verification: `47 passed` and `14 CI-rule tests OK`.
The direct-commit tests retain `db_commit` schema-reset isolation, and connection
lifetime tests retain independent file-backed SQLite/QueuePool behavior.

## Full suites and worker/connection budget

Baseline local original suite: `1051 passed`, zero skips, `557.91s`.
Final local serial shuffle seed6608: `1056 passed`, zero skips, `483.75s`.
Final local n2 shuffle seed6608: `1056 passed`, zero skips, `236.67s`.
JUnit testcase identifiers preserve all original1051 cases and add five isolation
regressions. Separate seeded runs produce the same collection order.
`-n auto`, `-n logical` and `-n 3` were rejected; `-n 2` succeeds.
Engine connection instrumentation observed a maximum of two SQLite DBAPI
connections per worker, four simultaneous in the shuffled job, all closed.
There are zero PostgreSQL connections in this job. This is a per-job budget;
other pipeline containers have their own independently isolated databases.
Default required Python tests remain serial. Daily shuffle is opt-in and has a
30-minute job timeout, xdist3.8.0, two workers and seedCI_PIPELINE_ID.

## Canonical CI and unchanged blocking security policies

Baseline MR pipeline [9578](https://git.entire.host/entire-vc/evc-team-relay/-/pipelines/9578)
(`f0f9b1e03b6f6d018476e1f8a04da40df38577db`) succeeded with13required jobs.
Candidate MR pipeline [9592](https://git.entire.host/entire-vc/evc-team-relay/-/pipelines/9592)
(`b9b2d59f0c9a16bbc3688d49ec9ff2b1e5105451`) succeeded with16required jobs.
All original13 jobs remain required, plus blocking CI contracts and both scans.
Both pipelines belong to the same MR. This documentation-only follow-up requires
its own green pipeline before merge; it does not change the tested implementation.

Canonical baseline Python output: `1051 passed, 15546 warnings in 574.54s`.
Canonical candidate Python output: `1056 passed, 15570 warnings in 529.51s`.
Semgrep1.155.0 uses a pinned image digest and `semgrep ci` with the existing
p/security-audit, p/secrets, p/owasp-top-ten and p/python policies, excluding
apps/relay-server/archive. The job is required (`allow_failure: false`). Trivy0.70.0 uses pinned image digest,
`--scanners vuln --severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed
--ignorefile .trivyignore`. Both jobs succeeded; ignores/policies were not
relaxed. The only dependency change is source-map-js1.2.1 to1.2.2 lock metadata.
Local scan with the exact configured Trivy policy ended `TRIVY_EXIT_CODE=0`.
The XML diagnostic uses actual malloc_info heap tags; zero tags fails closed.

## Deploy/release audit for this work

Audit window: 2026-10-08T15:15:00Z through 2026-10-08T16:14:38Z.
Successful read-only commands and observed content:

```text
glab api projects/10/pipelines/9592/bridges --paginate
[]
glab api projects/10/pipelines/9592/jobs?per_page=100
16 jobs: all success, all allow_failure=false, no deploy/release job

gh run list --repo entire-vc/evc-team-relay --workflow deploy.yml \
  --created '>=2026-10-08' --limit 100
[]
gh run list --repo entire-vc/evc-team-relay --workflow release.yml \
  --created '>=2026-10-08' --limit 100
[]
```

The GitHub commands also requested JSON fields databaseId, createdAt, event,
headSha, conclusion and workflowName. Every query exited0; empty lists cannot
be truncated by the requested limit. Positive controls on the same endpoints:
GitLab9089/bridges returned real `trigger:prod-team-relay` with downstream9098;
GitHub historical Deploy32643588933 (2026-08-23T13:50:41Z) and
Release37360228591 (2026-10-05T19:00:23Z) were visible without date filtering.
The actual implementation changeset selects no automatic deployment in either
CI implementation; runtime-path positive controls still select deployment.

This establishes that this work's recorded MR CI/workflow mechanisms did not
launch a deploy/release through the audit timestamp. It is not an assertion about
unrelated operators or arbitrary unrecorded SSH actions. No production runtime
or SSH action was performed for this work. The main/daily bridge audit must be
repeated after merge, before accepting the daily schedule.

## Same-MR runner, wall and queue comparison

| Job | Baseline runner s | Final runner s | Baseline queue s | Final queue s |
|---|---:|---:|---:|---:|
| alembic-migration-smoke | 95.380 | 117.201 | 103.365 | 1.728 |
| artifact-relocation-contract | 119.232 | 203.792 | 102.756 | 1.327 |
| browser-budget-svelte | 168.908 | 173.474 | 184.519 | 2.669 |
| bundle-budget-svelte | 91.021 | 159.964 | 142.046 | 1.951 |
| check-migration-drift | 103.458 | 190.185 | 118.172 | 1.788 |
| ci-rules-contract | added | 134.163 | added | 7.881 |
| hold-gate | 40.382 | 98.187 | 51.741 | 1.973 |
| lint-python | 67.254 | 59.757 | 2.349 | 0.789 |
| relay-server-tests | 171.782 | 150.185 | 12.459 | 0.787 |
| release-smoke-contract | 34.818 | 116.335 | 81.798 | 1.003 |
| semgrep-security | added | 149.869 | added | 0.417 |
| test-python | 620.497 | 666.156 | 103.083 | 1.654 |
| test-svelte | 102.694 | 89.877 | 118.413 | 1.927 |
| topology-gate | 36.500 | 119.605 | 69.582 | 10.751 |
| trivy-security | added | 150.466 | added | 0.571 |
| typecheck-svelte | 73.412 | 65.047 | 2.195 | 1.091 |

Original 13 jobs runner-seconds: 1725.337 → 2209.764.
Added 3 blocking gates runner-seconds: 434.498; final total: 2644.262.
Pipeline wall-clock created→finished: 907.863 → 877.698 s.
GitLab pipeline duration: 839 → 873 s; initial queue: 52 → 2 s.
Sum of job queue durations: 1092.477 → 38.309 s (overlapping waits; not added to wall-clock).

## Tests and limits
Canonical baseline Python: 1051 passed, 15546 warnings in 574.54s (0:09:34) ===============
Canonical final Python: 1056 passed, 15570 warnings in 529.51s (0:08:49) ===============

Identical original IDs retained: 1051; added isolation regressions: 5; skips: 0.
Same original 1051 testcases, local setup/call/teardown sum: 557.336 → 482.327 s (-13.46%).
Local serial full suite: baseline 1051/0,557.91s; optimized initial serial 1055/0,493.40s; final shuffled serial 1056/0,483.75s.
Local measurement uses the same Python3.14/Mac environment; canonical CI uses Python3.12/Linux. Local baseline overlapped narrow proof work, and CI cache/queue/runner contention differ. No causal or numerical target claim is made.
New tests and three required gates are reported as additions. Total pipeline speed reflects both optimization and those mandatory checks, not just fixture changes.
Full local shuffled n2 run: 1056 passed/0 skips. Explicit budget: two workers, measured maximum four simultaneous SQLite DBAPI connections, zero PostgreSQL connections.

## Actual gains and regressions
Canonical pytest body improved574.54→529.51s (-7.84%) despite five added tests. The entire Python job regressed620.497→666.156s (+7.36%); setup/other-job overhead by subtraction increased45.957→136.646s. Original13jobs cost increased28.08%; adding three blocking gates brought total runnercost+53.26% (1725.337→2644.262s). Pipeline wall-clock improved3.32% (907.863→877.698s), while GitLab active duration increased839→873s; lower queuing must not be attributed to the fixture optimization. The change improves test execution/isolation, but this full CI run uses more runner-seconds. Queue/cache/container-preparation differences prevent a causal overall-CI speedup claim.
