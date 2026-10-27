# Node monitor measurement kit (HUST 2026)

Operator scripts and parsers for **compute-node** captures: `hpcperfstatsd` cadence,
CPU/memory/I/O/network overhead, collector isolation, OS jitter, microbenchmarks,
and the hardware-error **catalog** (coverage vs reviewer classes — not a new collector).

**Runbook:** [RUNBOOK.md](RUNBOOK.md)

Paper-facing numbers still must not be invented. Copy JSON from `results/` into the
shared ledger under [`tests/site_measurement/results/`](../../tests/site_measurement/README.md)
and run `tests/site_measurement/bin/emit_paper_facts.py` before editing the `.tex`.

## Local checks (no production node)

From this directory, with the workspace `.venv` if available:

```bash
./bin/run_local_checks.sh
```

Or from the Cursor workspace root:

```bash
.venv/bin/python3 -m pytest HPCPerfStats/monitor/measurement/tests/ -q
```
