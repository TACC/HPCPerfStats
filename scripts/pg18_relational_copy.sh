#!/bin/sh
# Freeze-time copy of every public table except host_data / django_migrations
# from Hub PG15 (`db`) into PG18. Runs INSIDE db_pg18 (PG18 client tools), fed
# over stdin from the checkout (docs/how-to/operate/migrate-to-postgresql-18.md Phase D):
#
#   podman-compose -p hpcperfstats --profile pg18-migrate exec -T db_pg18 \
#     sh -s < scripts/pg18_relational_copy.sh
#
# Writers (web, pipeline) must be stopped and PG15 must be read-only
# (default_transaction_read_only = on). Steps, fail-closed:
#   0. refuse unless PG15 is read-only; delete the relational_copy_ok stamp
#   1. same public table set on both sides (Phase B migrate matches PG15)
#   2. parallel pg_dump -Fd (lz4) from db into the PG18 bind
#   3. TRUNCATE ... RESTART IDENTITY CASCADE of those tables on PG18
#   4. parallel pg_restore --data-only --disable-triggers (FK order free)
#   5. exact per-table counts must match PG15, then ANALYZE those tables
#   6. write the relational_copy_ok stamp (JSON: time and the PG18/PG15
#      system identifiers + PG18 database OID; tmp + mv) for
#      scripts/pg18_write_primary_marker.sh
# Ends with RELATIONAL_COPY_OK; any failure prints RELATIONAL_COPY_FAILED.
# PG18_RELATIONAL_ROOT (default /var/lib/postgresql, the PG18 bind root) holds
# the dump and the stamp.

set -eu
PGPASSWORD="${PGPASSWORD:-hpcperfstats}"
PGOPTIONS="-c statement_timeout=0"
export PGPASSWORD PGOPTIONS
JOBS="${PG18_RELATIONAL_JOBS:-8}"
ROOT="${PG18_RELATIONAL_ROOT:-/var/lib/postgresql}"
DUMP="${ROOT}/pg15_relational_dump"
STAMP="${ROOT}/relational_copy_ok"
SRC="-h db -U hpcperfstats -d hpcperfstats"
DST="-h localhost -U hpcperfstats -d hpcperfstats"
WHERE="schemaname = 'public' AND tablename NOT IN ('host_data', 'django_migrations')"

fail() {
  echo "RELATIONAL_COPY_FAILED: $*" >&2
  exit 1
}

# shellcheck disable=SC2086
read_only="$(psql $SRC -XtAc "SHOW default_transaction_read_only")" \
  || fail "cannot read PG15 default_transaction_read_only"
[ "${read_only}" = on ] || fail "PG15 default_transaction_read_only is" \
  "'${read_only}', not on (ALTER SYSTEM SET it and reload first)"
rm -f "${STAMP}" "${STAMP}.tmp" || fail "cannot remove ${STAMP}"

tables_sql="SELECT string_agg(quote_ident(tablename), ', ' ORDER BY tablename) FROM pg_tables WHERE ${WHERE}"
# shellcheck disable=SC2086
src_tables="$(psql $SRC -XtAc "${tables_sql}")" || fail "cannot list PG15 tables"
# shellcheck disable=SC2086
dst_tables="$(psql $DST -XtAc "${tables_sql}")" || fail "cannot list PG18 tables"
[ -n "${dst_tables}" ] || fail "no public tables on PG18 (Phase B migrate missing?)"
[ "${src_tables}" = "${dst_tables}" ] || fail "table sets differ: PG15=[${src_tables}] PG18=[${dst_tables}]"
echo "tables: ${dst_tables}"

rm -rf "${DUMP}"
start=$(date +%s)
# shellcheck disable=SC2086
pg_dump $SRC -Fd -j "${JOBS}" -Z lz4 --data-only --schema=public \
  --exclude-table-data=host_data --exclude-table-data=django_migrations \
  -f "${DUMP}" || fail "pg_dump from db"
echo "dump_s=$(($(date +%s) - start)) size=$(du -sh "${DUMP}" | cut -f1)"

# shellcheck disable=SC2086
psql $DST -X -v ON_ERROR_STOP=1 -c "TRUNCATE TABLE ${dst_tables} RESTART IDENTITY CASCADE" \
  || fail "truncate on PG18"
start=$(date +%s)
# shellcheck disable=SC2086
pg_restore $DST -j "${JOBS}" --data-only --disable-triggers --exit-on-error "${DUMP}" \
  || fail "pg_restore into PG18"
echo "restore_s=$(($(date +%s) - start))"
rm -rf "${DUMP}"

counts_sql="SELECT string_agg(tablename || '=' || (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM public.%I', tablename), false, true, '')))[1]::text, ' ' ORDER BY tablename) FROM pg_tables WHERE ${WHERE}"
# shellcheck disable=SC2086
src_counts="$(psql $SRC -XtAc "${counts_sql}")" || fail "count PG15"
# shellcheck disable=SC2086
dst_counts="$(psql $DST -XtAc "${counts_sql}")" || fail "count PG18"
echo "pg15: ${src_counts}"
echo "pg18: ${dst_counts}"
[ "${src_counts}" = "${dst_counts}" ] || fail "row counts differ"

# shellcheck disable=SC2086
psql $DST -X -v ON_ERROR_STOP=1 -c "ANALYZE ${dst_tables}" || fail "analyze"

id_sql="SELECT system_identifier FROM pg_control_system()"
# shellcheck disable=SC2086
pg15_id="$(psql $SRC -XtAc "${id_sql}")" || fail "read PG15 system_identifier"
# shellcheck disable=SC2086
pg18_id="$(psql $DST -XtAc "${id_sql}")" || fail "read PG18 system_identifier"
# shellcheck disable=SC2086
oid="$(psql $DST -XtAc "SELECT oid FROM pg_database WHERE datname = 'hpcperfstats'")" \
  || fail "read PG18 database oid"
for value in "${pg15_id}" "${pg18_id}" "${oid}"; do
  case "${value#-}" in
    '' | *[!0-9]*)
      fail "bad system_identifier/oid: PG15=[${pg15_id}] PG18=[${pg18_id}] oid=[${oid}]" ;;
  esac
done
now="$(date -u '+%Y-%m-%dT%H:%M:%SZ %s')"
{
  printf '{"stamp": "relational_copy_ok", "written_at": "%s", "written_at_epoch": %s, ' \
    "${now% *}" "${now#* }"
  printf '"pg18_system_identifier": "%s", "pg18_database_oid": %s, ' "${pg18_id}" "${oid}"
  printf '"pg15_system_identifier": "%s"}\n' "${pg15_id}"
} > "${STAMP}.tmp" || fail "cannot write ${STAMP}.tmp"
mv -f "${STAMP}.tmp" "${STAMP}" || fail "cannot rename ${STAMP}.tmp"
echo "stamp: ${STAMP}"
echo "RELATIONAL_COPY_OK"
