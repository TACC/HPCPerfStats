# Site and pipeline measurement runbook

Operator instructions for **AUTHOR_NOTES** items that need the **Django API**, **Compose**
stack, or author protocols (screenshots, tickets, roofline). Node monitor captures are
documented in [`monitor/measurement/RUNBOOK.md`](../../monitor/measurement/RUNBOOK.md).

Copy captures into `tests/site_measurement/results/` (gitignored). For paper slots that
include monitor JSON (`cadence.json`, `overhead.json`, …), copy those files from
`monitor/measurement/results/` into the same `results/` directory before emitting facts.

**Law:** if a script cannot measure it, the slot stays `NOT_MEASURED`.

Python: `<workspace>/.venv/bin/python3` on the operator host when running parser tests;
`python3` on nodes or inside compose exec blocks as noted below.

---

## 1. Website query latency (AUTHOR_NOTES §3)

Staff or user API key (`X-API-Key`). Base URL must include `/api/`.

```bash
cd tests/site_measurement   # from HPCPerfStats git checkout
export HPCPERFSTATS_API_URL='https://stats.example.edu/api/'
export HPCPERFSTATS_API_KEY='…'   # not committed
export HPCPERFSTATS_MEASURE_JID='2688'          # optional Job Detail
export HPCPERFSTATS_MEASURE_QUEUE='skx-normal'  # optional filter
export HPCPERFSTATS_MEASURE_USERNAME='someuser'

python3 bin/measure_query_latency.py --dry-run   # print URLs only
python3 bin/measure_query_latency.py \
  --repeats 5 --warmup 1 \
  --out results/query_latency.json
```

Scenarios live in `scenarios/query_latency.json`:

- Extended Search: 1 / 7 / 30 / 365 day `end_time` spans
- queue, username, `metrics_avg_freq__gte` threshold
- job-list histograms
- Job Detail (full and `light=1`) and Job plots

Job Detail paths are skipped (not measured) when `JID` is empty. Record the portal
hostname, date, and whether plots were prewarmed.

Times are **API wall-clock** (`GET /api/jobs/…`), not browser paint or Bokeh embed time.

---

## 2. Central pipeline scale (AUTHOR_NOTES §3)

From the **HPCPerfStats** checkout root that has `docker-compose.yaml`. Settings file
must exist. `capture_pipeline_scale.sh` writes paste blocks; run them as the operator.

```bash
./bin/capture_pipeline_scale.sh
```

Then paste **pipeline** (INI paths) and **db** (size + lag SQL) from `OPERATOR_BLOCKS.md`.
For ingest rows/s, pipe timestamped pipeline logs into
`scripts/measure_pipeline_ingest_rate.py`.

Fill `pipeline_scale.json` from that output. Leave nulls when a query fails.

### pipeline — INI paths first

```bash
docker compose exec pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
print(\"data_dir\", cfg.get_data_dir_path())
print(\"archive_dir\", cfg.get_archive_dir_path())
print(\"daily_archive_dir\", cfg.get_daily_archive_dir_path())
print(\"acct_path\", cfg.get_accounting_path())
"'
```

### db — size and lag proxy

Use the SQL block in the generated `OPERATOR_BLOCKS.md` (one `psql` invocation).

### pipeline — ingest rate logs

```bash
docker compose logs --timestamps --since 4h pipeline
```

---

## 3. Storage HA / RPO (AUTHOR_NOTES §4)

```bash
./bin/capture_storage_ha.sh
```

Fill `storage_ha.json` from site practice. The kit will not invent a replica count or RPO.
`pg_dump` in `docs/OPERATOR_PG18_MIGRATION.md` is a **cutover** procedure, not HA.

### db — WAL settings (not a backup policy)

```bash
docker compose exec db psql -h localhost -U hpcperfstats -d hpcperfstats -c "SHOW wal_level;" -c "SHOW archive_mode;" -c "SHOW archive_command;" -c "SHOW max_wal_senders;" -c "SELECT pg_is_in_recovery();"
```

### pipeline — archive paths

```bash
docker compose exec pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
print(\"archive_dir\", cfg.get_archive_dir_path())
print(\"daily_archive_dir\", cfg.get_daily_archive_dir_path())
"'
```

---

## 4. Screenshots, tickets, Figure 4 (AUTHOR_NOTES §6–8)

- `protocols/screenshots.md`
- `protocols/ticket_time_study.md`
- `protocols/figure4_roofline.md`

---

## 5. Paper-facing ledger

After monitor and site JSON files share one `results/` directory:

```bash
python3 bin/emit_paper_facts.py \
  --results-dir results \
  --out-md results/PAPER_SLOTS.md \
  --out-json results/paper_facts.json
```

Edit the `.tex` only for slots that are no longer `NOT_MEASURED`.
