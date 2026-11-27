#!/usr/bin/env bash
# Write compose operator blocks and optional live captures for pipeline scale.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

OUT=""
EXEC=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --exec) EXEC=1; shift ;;
    -h|--help)
      cat <<'EOF'
Usage: capture_pipeline_scale.sh [--out DIR] [--exec]

Writes copy/paste Compose blocks (INI first, one block per service) for:
  - TimescaleDB / host_data size
  - newest host_data timestamps (ingest freshness)
  - jobs ended recently vs metrics_data (update_metrics lag proxy)
  - pointer to HPCPerfStats/scripts/measure_pipeline_ingest_rate.py

--exec only runs when docker compose project hpcperfstats is already up on
this machine. Default is print-only so the paper kit cannot invent numbers.
EOF
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ -z "${OUT}" ]]; then
  OUT="$(stamp_dir "${ROOT}" pipeline)"
else
  mkdir -p "${OUT}"
fi

cat > "${OUT}/OPERATOR_BLOCKS.md" <<'EOF'
# Pipeline scale — operator paste blocks

Run from the git checkout that contains `docker-compose.yaml` (typically
`HPCPerfStats/`). Do not prefix these with `cd`. Settings file must exist.

Paste each block's stdout back into this capture directory (or let `--exec`
write `pipeline_scale.json` when compose is local).

## pipeline — resolve INI paths first

```bash
docker compose exec pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
print(\"data_dir\", cfg.get_data_dir_path())
print(\"archive_dir\", cfg.get_archive_dir_path())
print(\"daily_archive_dir\", cfg.get_daily_archive_dir_path())
print(\"acct_path\", cfg.get_accounting_path())
"'
```

## db — Timescale size, newest host_data, update_metrics lag proxy

```bash
docker compose exec db psql -h localhost -U hpcperfstats -d hpcperfstats -v ON_ERROR_STOP=1 <<'SQL'
SET statement_timeout = '60s';
SELECT current_database() AS db,
       pg_size_pretty(pg_database_size(current_database())) AS db_pretty,
       pg_database_size(current_database()) AS db_bytes;
SELECT hypertable_schema, hypertable_name,
       pg_size_pretty(hypertable_size(format('%I.%I', hypertable_schema, hypertable_name)::regclass)) AS pretty,
       hypertable_size(format('%I.%I', hypertable_schema, hypertable_name)::regclass) AS bytes
  FROM timescaledb_information.hypertables
 ORDER BY bytes DESC;
SELECT max(time) AS newest_host_data,
       extract(epoch from (now() - max(time))) AS host_data_lag_s
  FROM host_data;
SELECT count(*) FILTER (WHERE end_time > now() - interval '24 hours') AS jobs_ended_24h
  FROM job_data;
SELECT count(*) AS jobs_ended_24h_missing_metrics
  FROM job_data j
  LEFT JOIN metrics_data m ON m.jid = j.jid
 WHERE j.end_time > now() - interval '24 hours'
   AND m.jid IS NULL;
SQL
```

If `hypertable_size` is unavailable, use `pg_total_relation_size('host_data')`
only and record that limitation in `pipeline_scale.json`.

## pipeline — ingest rate from timestamped logs

```bash
docker compose logs --timestamps --since 4h pipeline
```

Then, from the HPCPerfStats checkout (not this paper kit):

```bash
docker compose logs --timestamps --since 4h pipeline \
  | python3 scripts/measure_pipeline_ingest_rate.py
```

Record rows/s or files/day from that script. Do not mix capped queue depth
with on-disk pending census (see the script docstring).
EOF

cat > "${OUT}/pipeline_scale.json" <<'EOF'
{
  "ingest_rows_per_s": null,
  "host_data_bytes": null,
  "db_bytes": null,
  "update_metrics_lag_s": null,
  "host_data_lag_s": null,
  "jobs_ended_24h_missing_metrics": null,
  "notes": "Fill from OPERATOR_BLOCKS.md output. Leave null when not measured."
}
EOF

if [[ "${EXEC}" -eq 1 ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    log "WARN: docker not found; left template JSON"
  else
    log "Attempting live db size query (compose project must already be up)"
    set +e
    docker compose exec db psql -h localhost -U hpcperfstats -d hpcperfstats -c \
      "SELECT pg_database_size(current_database()) AS db_bytes;" \
      > "${OUT}/db_size.txt" 2>"${OUT}/db_size.err"
    set -e
    log "Live exec finished; inspect ${OUT}/db_size.txt (do not invent on failure)"
  fi
fi

log "Wrote ${OUT}"
printf '%s\n' "${OUT}"
