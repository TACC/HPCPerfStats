#!/usr/bin/env bash
# Catalog storage HA / RPO facts. Does not invent a backup contract.
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
Usage: capture_storage_ha.sh [--out DIR] [--exec]

Writes the operator questionnaire and optional live SHOW commands for
archive_mode / wal_level. Production backup cadence and a quantified RPO
must be filled by operators from site practice — pg_dump in
docs/OPERATOR_PG18_MIGRATION.md is a cutover procedure, not an HA contract.
EOF
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ -z "${OUT}" ]]; then
  OUT="$(stamp_dir "${ROOT}" storage)"
else
  mkdir -p "${OUT}"
fi

cp "${ROOT}/templates/storage_ha_answers.json" "${OUT}/storage_ha.json"

cat > "${OUT}/OPERATOR_BLOCKS.md" <<'EOF'
# Storage HA / RPO — operator paste blocks

Architecture this paper can already state (do not contradict):

- Compose `db` is a single TimescaleDB/PostgreSQL volume.
- Host archives (raw then sealed `.tar.zst`) remain the source of truth until
  ingest + janitor succeed.
- `docs/OPERATOR_PG18_MIGRATION.md` `pg_dump` is a cutover procedure, not a
  measured HA contract.

## db — WAL / archive settings (not a backup policy)

```bash
docker compose exec db psql -h localhost -U hpcperfstats -d hpcperfstats -c "SHOW wal_level;" -c "SHOW archive_mode;" -c "SHOW archive_command;" -c "SHOW max_wal_senders;" -c "SELECT pg_is_in_recovery();"
```

## pipeline — confirm archive and daily-archive paths from INI

```bash
docker compose exec pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
print(\"archive_dir\", cfg.get_archive_dir_path())
print(\"daily_archive_dir\", cfg.get_daily_archive_dir_path())
"'
```

Fill `storage_ha.json` keys (`backup_cadence`, `wal_pitr`, `replicas`, `rpo`,
`restore_drill_date`) with site facts only. Use the string `NOT_MEASURED` or
JSON null when unknown.
EOF

if [[ "${EXEC}" -eq 1 ]]; then
  set +e
  docker compose exec db psql -h localhost -U hpcperfstats -d hpcperfstats \
    -c "SHOW wal_level;" -c "SHOW archive_mode;" \
    > "${OUT}/postgres_wal.txt" 2>"${OUT}/postgres_wal.err"
  set -e
fi

log "Wrote ${OUT}"
printf '%s\n' "${OUT}"
