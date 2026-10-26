#!/usr/bin/env bash
# Host-side sidecar sampler for loaded48 soaks (Phase 2 wait attribution).
# Read-only: samples container CPU/memory and Postgres wait events while the
# soak child runs, then exits when the child is gone.
#
# Usage: sample_phase2.sh OUT_PREFIX [interval_s]
# Writes:
#   OUT_PREFIX.containers.tsv  epoch, container, cpu_pct, mem
#   OUT_PREFIX.pgwait.tsv      epoch, wait_type, wait_event, state, n
#
# Test/benchmark only (rootless podman test project); not a production tool.
set -u
PREFIX="${1:?out prefix}"
INT="${2:-15}"
STATS="${PREFIX}.containers.tsv"
PGW="${PREFIX}.pgwait.tsv"
printf 'epoch\tname\tcpu_pct\tmem\n' >"$STATS"
printf 'epoch\twait_type\twait_event\tstate\tn\n' >"$PGW"

pid=""
for _ in $(seq 1 600); do
  pid="$(pgrep -f loaded48_supervisor_child.py | head -1 || true)"
  [[ -n "$pid" ]] && break
  sleep 2
done
[[ -z "$pid" ]] && { echo "soak child never appeared" >&2; exit 1; }

DB="$(podman ps --format '{{.Names}}' | grep -m1 '_db_1$' || true)"
while kill -0 "$pid" 2>/dev/null; do
  ep="$(date +%s)"
  podman stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null |
    while IFS=$'\t' read -r name cpu mem; do
      printf '%s\t%s\t%s\t%s\n' "$ep" "$name" "${cpu%\%}" "$mem"
    done >>"$STATS"
  if [[ -n "$DB" ]]; then
    podman exec "$DB" psql -U hpcperfstats -d hpcperfstats -At -F $'\t' -c \
      "SELECT coalesce(wait_event_type,'cpu'), coalesce(wait_event,'-'), state, count(*) FROM pg_stat_activity WHERE datname='hpcperfstats' AND pid<>pg_backend_pid() AND backend_type='client backend' GROUP BY 1,2,3" \
      2>/dev/null | awk -v ep="$ep" -F'\t' '{print ep"\t"$0}' >>"$PGW"
  fi
  sleep "$INT"
done
