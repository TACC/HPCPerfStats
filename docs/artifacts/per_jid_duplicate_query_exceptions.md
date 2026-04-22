# Per-JID Duplicate Query Exceptions

This register tracks any remaining duplicate per-jid DB query path that is intentionally retained.

Policy:
- No new duplicate per-jid DB path unless measurable efficiency gains are demonstrated.
- Each entry must include a re-evaluation date.

## Active Exceptions

1. `type_detail` per-type provider rebuilds in `persist_job_detail_artifacts_for_jid`
- Location: `hpcperfstats/site/machine/job_detail_artifacts.py`
- Duplicate behavior: creates `TypeDetailDataProvider` and plot inputs per type, which can re-touch host_data repeatedly for large schemas.
- Why retained: direct reuse of metrics-worker arrays is blocked by worker/process lifecycle separation and payload-shape coupling of type-detail API output.
- Measured benefit for keeping current path: avoids introducing a large serialized intermediate payload that would increase memory pressure and cross-process transfer cost during metrics runs.
- Re-evaluate by: 2026-06-01

2. Plot-kind specific aggregate probes across `summary_plot` and `heatmap`
- Location: `hpcperfstats/analysis/plot/*` through `persist_job_plot_artifacts_for_jid`
- Duplicate behavior: multiple plot builders probe related aggregates independently.
- Why retained: each plot has distinct fallback precedence and no-data contracts; over-aggressive consolidation risks behavior drift.
- Measured benefit for keeping current path: with `_JtMemoProxy`, repeated exact aggregate requests are now cache hits while preserving plot contracts.
- Re-evaluate by: 2026-06-01
