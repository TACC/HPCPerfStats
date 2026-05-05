# Rule Compliance Report (Current Pass)

## Implemented

- Compose/runtime contract fixes: nginx bind path, app example parity, configurable pipeline SSH mount.
- Cache correctness: fingerprinted `JOB_PLOTS_DATA` keys and logged fallback on cache write failures.
- File locking correctness: read lock no-unlink, active-lock-safe stale cleanup, repeated-cycle soak test.
- Migration safety: hardened `0001` SQL idempotence, runtime-safe `0002` grant SQL, added `0020` host_data PK contract alignment migration.
- Contract tests: AMD monitor typename prerequisites, GPU source precedence behavior.
- Completeness tests:
  - pipeline payload generator now includes AMD/ARM/AMD GPU typenames;
  - multihost payload publish helper and multihost assertions in pipeline ingest test;
  - plot-kind matrix assertions (`summary_plot`, `heatmap`, `roofline`, `gpu_roofline`) in browser/API pipeline test;
  - endpoint status-band tightening for critical HTML routes;
  - variable metadata parity/sync/regeneration header-path tests;
  - scientific-notation assertions on job detail + plot API payloads in pipeline browser test.
- Non-logic hygiene:
  - authoritative `hpcperfstats/cursor-rules` path references fixed in docs/generator outputs;
  - conflict artifact rule file removed;
  - logic-change checklist added;
  - incremental frontend naming plan documented;
  - CSP policy tightened (`unsafe-eval` removed from enforced CSP; stricter report-only policy).

## Residual Risk

- Compose-network E2E checks are environment-gated and may skip locally if compose network flag/tooling is absent.
- Migration behavior in fully compressed Timescale environments remains guarded by no-op early returns; rollout should validate on representative production-like DB state.
- Frontend naming plan is intentionally deferred and incremental to avoid merge churn.
