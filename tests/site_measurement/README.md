# Site and pipeline measurement kit (HUST 2026)

Operator scripts for **central stack** captures: Compose pipeline scale, storage HA
facts, portal **API** query latency, ticket-time study helpers, screenshots/roofline
protocols, and the paper-facing `emit_paper_facts.py` ledger.

**Runbook:** [RUNBOOK.md](RUNBOOK.md)

Node-side captures (cadence, pidstat overhead, jitter, microbenches, hardware-error
catalog) live under [`monitor/measurement/`](../../monitor/measurement/README.md).

## Local checks (no production API or compose required)

From this directory:

```bash
./bin/run_local_checks.sh
```

Or from the Cursor workspace root:

```bash
.venv/bin/python3 -m pytest HPCPerfStats/tests/site_measurement/tests/ -q
```

## What this kit measures

| AUTHOR_NOTES item | Capture | Notes |
|---|---|---|
| 3 Pipeline scale | `bin/capture_pipeline_scale.sh` | Compose operator blocks (INI first). |
| 3 Query latency | `bin/measure_query_latency.py` | Named Extended Search spans + Job Detail. |
| 4 Storage HA / RPO | `bin/capture_storage_ha.sh` | Catalog facts; does not invent a backup contract. |
| 6 Full-portal screenshots | `protocols/screenshots.md` | `job_list_full.png`, `job_detail_full.png`. |
| 7 Ticket-time study | `protocols/ticket_time_study.md` | CSV → `summarize_ticket_time.py`. |
| 8 Figure 4 roofline | `protocols/figure4_roofline.md` | Evidence checklist. |
| All slots | `bin/emit_paper_facts.py` | Merges JSON from `results/` (include monitor copies). |

Monitor-side rows: see `monitor/measurement/README.md`.
