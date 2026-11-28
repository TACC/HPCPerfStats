#!/bin/sh
# Entrypoint wrapper for the Hub PG15 `db` on legacy sites
# (docker-compose.pg15-primary.yaml bind-mounts it read-only and runs it with
# /bin/sh). Refuses before the Hub entrypoint chowns or chmods anything when
# the bind is not the PG15 cluster, was retired at cutover, or another server
# already holds it; then execs the Hub entrypoint with the lock on fd 9, which
# survives its gosu hop and the final exec postgres.
# Runbook: docs/how-to/operate/migrate-to-postgresql-18.md.
set -eu

data="${PGDATA:-/var/lib/postgresql/data}"

refuse() {
  printf >&2 'hpcperfstats pg15 guard: refusing to start: %s\n' "$1"
  printf >&2 '  path: %s\n' "$2"
  printf >&2 '  see docs/how-to/operate/migrate-to-postgresql-18.md\n'
  exit 1
}

if [ ! -f "${data}/PG_VERSION" ]; then
  refuse "no PG_VERSION: legacy db only serves an existing PG15 cluster" \
    "${data}/PG_VERSION"
fi
version="$(cat "${data}/PG_VERSION")"
if [ "${version}" != 15 ]; then
  refuse "PG_VERSION is '${version}', this server is PostgreSQL 15" \
    "${data}/PG_VERSION"
fi
for name in hpcperfstats-primary hpcperfstats-migrate-target 18; do
  if [ -e "${data}/${name}" ]; then
    refuse "${name} present: this is a PG18 bind" "${data}/${name}"
  fi
done
if [ -e "${data}/hpcperfstats-retired" ]; then
  refuse "hpcperfstats-retired present: this site cut over to PG18" \
    "${data}/hpcperfstats-retired"
fi
if ! { exec 9>>"${data}/.hpc-server.lock"; } 2>/dev/null; then
  refuse "cannot create the lock file (NFS binds are not supported)" \
    "${data}/.hpc-server.lock"
fi
flock -n 9 || refuse "another server holds the lock on this data folder" \
  "${data}/.hpc-server.lock"
exec docker-entrypoint.sh "$@"
