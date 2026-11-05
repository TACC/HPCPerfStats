#!/bin/bash
# Parallel repair of per-core host_data rows (collapse_percore_host_data --apply, sharded by host).
#   collapse_percore_parallel.sh auto [SINCE_DAY]     stop any run, survey, repair every day with per-core rows,
#                                                     retry days the merge holds; stays open, status every 5 min
#   collapse_percore_parallel.sh stop                 stop every repair (runner, jobs, DB sessions; never the psql merge)
#   collapse_percore_parallel.sh start DAY [DAY ...]  shard each day by host and run collapse_percore_host_data --apply
#   collapse_percore_parallel.sh restart              stop the current run and start its days again
#   collapse_percore_parallel.sh resume-policy        re-enable the compression policy start paused
#   collapse_percore_parallel.sh status               progress of the current run
#   collapse_percore_parallel.sh survey [SINCE_DAY]   read-only: deployed fix, writers, WAL, per-day per-core share
set -uo pipefail

RUN=${COLLAPSE_RUN_DIR:-$HOME/collapse_run}
DB=${COLLAPSE_DB_CONTAINER:-hpcperfstats_db_1}
PIPE=${COLLAPSE_PIPELINE_CONTAINER:-hpcperfstats_pipeline_1}
PAR=${COLLAPSE_PARALLEL:-4}
POLL_S=${COLLAPSE_POLL_S:-60}
STATUS_S=${COLLAPSE_STATUS_S:-300}
MERGE_RETRY_S=${COLLAPSE_MERGE_RETRY_S:-300}
SHARD_ROWS=${COLLAPSE_SHARD_ROWS:-100000000}
MANAGE=${COLLAPSE_MANAGE:-/home/hpcperfstats/hpcperfstats/site/manage.py}
SELF=$(readlink -f "$0")

die() { echo "ERROR: $*" >&2; exit 1; }
# psql inside the db container, first argument the PGOPTIONS. -w never prompts:
# with no terminal a password prompt waits forever (hpcperfstats04 hung 10 h on
# one). The password comes from the container's PGPASSWORD or POSTGRES_PASSWORD.
psql_in_db() {
  local opts=$1; shift
  podman exec -i -e PGOPTIONS="$opts" "$DB" sh -c \
    'PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}" exec psql -w "$@"' sh \
    -U hpcperfstats -d hpcperfstats -X -At "$@"
}
psql_q() { psql_in_db '-c statement_timeout=0' -v ON_ERROR_STOP=1 "$@"; }
repair_count() {
  local n
  n=$(podman exec "$PIPE" pgrep -fc collapse_percore_host_data 2>/dev/null)
  echo "${n:-0}"
}
valid_day() { [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; }
current() { readlink -f "$RUN/current" 2>/dev/null; }
# Copy this checkout's repair command into the pipeline container, so a fix
# to it reaches the jobs with a git pull instead of an image rebuild.
CMD_REL=site/lib/machine/management/commands/collapse_percore_host_data.py
sync_command() {
  local src="$(dirname "$SELF")/../hpcperfstats/$CMD_REL"
  local dst="${MANAGE%/site/manage.py}/$CMD_REL"
  [ -f "$src" ] || { echo "no repair command at $src" >&2; return 1; }
  podman cp "$src" "$PIPE:$dst" || return 1
  echo "repair command copied from this checkout into $PIPE:$dst"
}
# FIXED when the pipeline image has the dbcabf0e collapse fix and the repair command.
fix_state() {
  podman exec "$PIPE" python3 -c "
import importlib.util as u
m = u.find_spec('hpcperfstats.dbload.lib.sync_timedb_parsing')
c = u.find_spec('hpcperfstats.site.lib.machine.management.commands.collapse_percore_host_data')
if c is None: print('NO_REPAIR_COMMAND')
elif 'sum across devices to' not in open(m.origin).read(): print('NOT_FIXED')
else: print('FIXED')
" 2>/dev/null || echo "PIPELINE_UNREACHABLE"
}
# Sessions running the repair command's own statements (prefixes of the SQL in
# collapse_percore_host_data.py). stop cancels only these: a broader host_data
# filter also cancelled live update_metrics queries on hpcperfstats04.
REPAIR_SESSIONS="datname = current_database() AND pid <> pg_backend_pid() AND state <> 'idle'
  AND application_name <> 'psql' AND query LIKE ANY (ARRAY[
  'SELECT time, host, jid, type, dev, event, unit, value, delta, arc FROM host_data %',
  'SELECT host, type, event, time FROM host_data %',
  'SELECT DISTINCT host, type, event, time FROM host_data %',
  'SELECT DISTINCT host FROM host_data WHERE time >= %',
  'SELECT DISTINCT date_trunc(''day'', time AT TIME ZONE ''UTC'') FROM host_data %',
  'SELECT host, type, event, time, value, delta FROM host_data %',
  'INSERT INTO host_data (time, host, jid, type, dev, event, unit, value, delta, arc) VALUES %',
  'DELETE FROM host_data WHERE host = ANY%',
  'DELETE FROM host_data hd WHERE hd.host = ANY%',
  'WITH RECURSIVE h AS (%'])"
# Host-file content for a job that runs without --host (a compressed day).
ALL_HOSTS="__ALL_HOSTS_FOUND_PER_WINDOW__"
POLICY_NOTE="$RUN/compression_policy_paused"
# Pause an enabled compression policy for the repair (it would compress days
# before they are repaired, and compress_chunk blocks the repair); note its
# job id so resume-policy turns it back on. A paused or absent policy is left.
pause_compression_policy() {
  local row
  row=$(psql_q -c "SELECT job_id || ' ' || scheduled FROM timescaledb_information.jobs
    WHERE hypertable_name = 'host_data' AND proc_name = 'policy_compression' LIMIT 1;")
  [ "${row#* }" = "t" ] || return 0
  psql_q -c "SELECT alter_job(${row%% *}, scheduled => false);" > /dev/null || die "could not pause compression policy ${row%% *}"
  mkdir -p "$RUN" && echo "${row%% *}" > "$POLICY_NOTE"
  echo "paused compression policy job ${row%% *} for the repair; turn it back on with: $0 resume-policy"
}
resume_policy() {
  [ -s "$POLICY_NOTE" ] || { echo "no compression policy was paused by this script"; return 0; }
  local job; job=$(cat "$POLICY_NOTE")
  psql_q -c "SELECT alter_job($job, scheduled => true);" > /dev/null || die "could not resume compression policy $job"
  rm -f "$POLICY_NOTE"
  echo "compression policy job $job is scheduled again"
}

# One shard: invoked by xargs as "SELF _job DAY HOSTFILE".
job() {
  local day=${1:-} file=${2:-} log rc host
  local -a args=()
  if ! valid_day "$day" || [ ! -s "$file" ]; then
    echo "REFUSED day=[$day] file=[$file]" >> "$RUN/refused.log"
    return 0
  fi
  if [ "$(cat "$file")" != "$ALL_HOSTS" ]; then
    while IFS= read -r host; do
      [[ "$host" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "REFUSED bad host [$host] in $file" >> "$RUN/refused.log"; return 0; }
      args+=(--host "$host")
    done < "$file"
  fi
  log="$(dirname "$file")/logs/$(basename "$file").log"
  echo "start $(date -u +%FT%TZ) day=$day hosts=${#args[@]}" > "$log"
  # --skip-checks: Django's model field checks connect to every configured
  # database (e.g. an unreachable xalt MySQL host) and abort the command.
  podman exec "$PIPE" python3 "$MANAGE" collapse_percore_host_data \
    --skip-checks --since "$day" --until "$day" "${args[@]}" --apply >> "$log" 2>&1
  rc=$?
  echo "rc=$rc end $(date -u +%FT%TZ)" >> "$log"
  return 0
}

stop() {
  # Runners first (so they start no new job), then the jobs: this script's and
  # the hand-built $RUN/run_one.sh runner used on hpcperfstats02.
  pkill -f "xargs .*-L 1 .*($SELF _job|$RUN/run_one.sh)" 2>/dev/null
  pkill -f "$SELF _job" 2>/dev/null
  pkill -f "$RUN/run_one.sh" 2>/dev/null
  podman exec "$PIPE" pkill -f collapse_percore_host_data 2>/dev/null
  sleep 3
  echo "== cancelling repair DB sessions (only the repair command's own statements)"
  psql_q -c "SELECT pid, now()-query_start, left(query, 80), pg_cancel_backend(pid)
    FROM pg_stat_activity WHERE $REPAIR_SESSIONS;"
  local i left
  for i in $(seq 1 12); do
    left=$(psql_q -c "SELECT count(*) FROM pg_stat_activity WHERE $REPAIR_SESSIONS;")
    [ "$left" = "0" ] && break
    sleep 5
  done
  echo "repair processes left=$(repair_count) repair DB sessions left=${left:-?}"
}

start() {
  [ $# -ge 1 ] || die "usage: $0 start DAY [DAY ...]"
  local day
  for day in "$@"; do valid_day "$day" || die "bad day [$day]"; done
  [ "$(repair_count)" = "0" ] || die "a repair process is already running; run '$0 stop' first"
  local slots arch merge_days
  slots=$(psql_q -c "SELECT count(*) FROM pg_replication_slots;") || die "psql failed"
  arch=$(psql_q -c "SHOW archive_mode;") || die "psql failed"
  [ "$slots" = "0" ] && [ "$arch" = "off" ] || die "replication_slots=$slots archive_mode=$arch (WAL would pile up)"
  local fix; fix=$(fix_state)
  [ "$fix" = "FIXED" ] || die "pipeline code state is $fix; deploy dbcabf0e or later before repairing"
  sync_command || die "could not copy the repair command into $PIPE"
  # Compressed days are repaired in place (the command lifts Timescale's DML
  # decompression cap); slower, but no decompress_chunk lock blocks the run.
  pause_compression_policy
  merge_days=$(psql_q -c "SELECT DISTINCT c.range_start::date FROM pg_locks l
    JOIN pg_stat_activity a ON a.pid = l.pid JOIN pg_class cl ON cl.oid = l.relation
    JOIN timescaledb_information.chunks c ON c.chunk_name = cl.relname AND c.hypertable_name = 'host_data'
    WHERE l.locktype = 'relation' AND a.pid <> pg_backend_pid() AND a.query ILIKE 'insert into host_data%';") || die "psql failed"
  local -a todo=()
  for day in "$@"; do
    if grep -qx "$day" <<< "$merge_days"; then
      echo "SKIPPED $day: the merge is writing to it; run '$0 start $day' after the merge commits"
    else todo+=("$day"); fi
  done
  [ ${#todo[@]} -gt 0 ] || die "the merge is writing to every requested day; start them after it commits"
  set -- "${todo[@]}"

  local dir est nh k f
  dir="$RUN/run_$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$dir/logs" || die "cannot create $dir"
  : > "$dir/jobs"
  for day in "$@"; do
    # A compressed chunk has no usable (host, time) index, so the host walk
    # below unpacks the chunk once per host (hours per day). Give the day one
    # job without --host instead: the command finds each window's per-core
    # hosts itself with a window-bounded query, which unpacks only that hour.
    if [ "$(psql_q -c "SELECT coalesce(bool_or(is_compressed), false) FROM timescaledb_information.chunks
        WHERE hypertable_name = 'host_data' AND range_start = '$day 00:00+00';")" = "t" ]; then
      echo "$ALL_HOSTS" > "$dir/shard_${day}_all"
      echo "0 $day $dir/shard_${day}_all" >> "$dir/jobs"
      echo "$day compressed: one job, hosts found per window"
      continue
    fi
    psql_q > "$dir/hosts_$day.txt" <<SQL || die "host list query failed for $day"
WITH RECURSIVE h AS (
  (SELECT host FROM host_data WHERE time >= '$day 00:00+00' AND time < '$day 00:00+00'::timestamptz + interval '1 day'
     AND host IS NOT NULL ORDER BY host LIMIT 1)
  UNION ALL
  SELECT (SELECT d.host FROM host_data d WHERE d.time >= '$day 00:00+00'
            AND d.time < '$day 00:00+00'::timestamptz + interval '1 day' AND d.host > h.host ORDER BY d.host LIMIT 1)
  FROM h WHERE h.host IS NOT NULL)
SELECT host FROM h WHERE host IS NOT NULL;
SQL
    est=$(psql_q -c "SELECT coalesce(max(cl.reltuples), 0)::bigint FROM timescaledb_information.chunks c
      JOIN pg_namespace ns ON ns.nspname = c.chunk_schema JOIN pg_class cl ON cl.relnamespace = ns.oid AND cl.relname = c.chunk_name
      WHERE c.hypertable_name = 'host_data' AND c.range_start = '$day 00:00+00';") || die "row estimate failed for $day"
    nh=$(grep -c . "$dir/hosts_$day.txt")
    if [ "$nh" -eq 0 ]; then echo "$day no hosts, skipped"; continue; fi
    k=$(( (est + SHARD_ROWS - 1) / SHARD_ROWS )); [ "$k" -lt 1 ] && k=1; [ "$k" -gt "$nh" ] && k=$nh
    split -n r/"$k" -d -a 2 "$dir/hosts_$day.txt" "$dir/shard_${day}_" || die "split failed for $day"
    for f in "$dir"/shard_"${day}"_*; do
      [ -s "$f" ] && echo "$(( est / k )) $day $f" >> "$dir/jobs"
    done
    echo "$day est_rows=$est hosts=$nh shards=$k"
  done
  [ -s "$dir/jobs" ] || die "no jobs built"
  sort -rn "$dir/jobs" | awk 'NF == 3 {print $2, $3}' > "$dir/queue"
  ln -sfn "$dir" "$RUN/current"
  : > "$RUN/refused.log"
  setsid nohup xargs -r -P "$PAR" -L 1 "$SELF" _job < "$dir/queue" > "$dir/runner.log" 2>&1 &
  echo "STARTED $(wc -l < "$dir/queue") jobs, $PAR at a time, runner pid=$! dir=$dir"
}

status() {
  local dir f total done_n
  dir=$(current); [ -n "$dir" ] || die "no run yet"
  total=$(wc -l < "$dir/queue")
  done_n=$(grep -l '^rc=' "$dir"/logs/*.log 2>/dev/null | wc -l)
  echo "dir=$dir jobs=$total finished=$done_n repair_processes=$(repair_count)"
  echo "== failed jobs"
  grep -l '^rc=[1-9]' "$dir"/logs/*.log 2>/dev/null
  grep -h -m1 -E 'CommandError|Traceback' "$dir"/logs/*.log 2>/dev/null | head -5
  cat "$RUN/refused.log" "$dir/runner.log" 2>/dev/null | head -5
  echo "== finished totals"
  grep -h '^TOTAL' "$dir"/logs/*.log 2>/dev/null \
    | awk '{for (i = 2; i <= NF; i++) {split($i, a, "="); s[a[1]] += a[2]}} END {for (k in s) printf "%s=%d ", k, s[k]; print ""}'
  echo "== running jobs (last line)"
  for f in $(grep -L '^rc=' "$dir"/logs/*.log 2>/dev/null); do
    printf '%s  %s\n' "$(basename "$f" .log)" "$(tail -1 "$f" | cut -c1-110)"
  done
}

survey() {
  local since=${1:-2026-09-15} c
  valid_day "$since" || die "bad day [$since]"
  echo "== host=$(hostname) containers"; podman ps --format '{{.Names}}  {{.Status}}'
  echo "== pipeline code: $(fix_state)   repair processes running: $(repair_count)"
  echo "== replication_slots=$(psql_q -c 'SELECT count(*) FROM pg_replication_slots;') archive_mode=$(psql_q -c 'SHOW archive_mode;')"
  echo "== non-idle host_data sessions"
  psql_q -c "SELECT pid, application_name, now()-query_start, left(query, 80) FROM pg_stat_activity
    WHERE datname = current_database() AND pid <> pg_backend_pid() AND state <> 'idle' AND query ILIKE '%host_data%';"
  echo "== per day since $since: day|compressed|size|est_rows|pct_percore (0.01% sample; compressed: has_percore within 60 s)"
  psql_q -F '|' -c "SELECT c.range_start::date, c.is_compressed, format('%I.%I', c.chunk_schema, c.chunk_name),
      pg_size_pretty(pg_total_relation_size(format('%I.%I', c.chunk_schema, c.chunk_name)::regclass)), cl.reltuples::bigint
    FROM timescaledb_information.chunks c JOIN pg_namespace ns ON ns.nspname = c.chunk_schema
    JOIN pg_class cl ON cl.relnamespace = ns.oid AND cl.relname = c.chunk_name
    WHERE c.hypertable_name = 'host_data' AND c.range_start >= '$since 00:00+00' ORDER BY 1;" \
  | while IFS='|' read -r day comp chunk size est; do
      if [ "$comp" = "f" ]; then
        c=$(psql_q -c "SELECT coalesce(round(100.0 * avg(CASE WHEN dev <> '' AND dev NOT LIKE 'j:%' AND type NOT IN ('nvidia_gpu','amd_gpu','intel_gpu') THEN 1 ELSE 0 END), 1)::text, 'empty') FROM $chunk TABLESAMPLE SYSTEM (0.01) REPEATABLE (7);" < /dev/null)
      else
        # Three one-hour slices, not the whole day: compressed batches carry
        # their time range, so only those slices are unpacked. A whole-day
        # EXISTS timed out (and logged an ERROR) on every large chunk.
        c=$(psql_in_db '-c statement_timeout=60000' \
          -c "SELECT 'has_percore=' || EXISTS (SELECT 1 FROM $chunk WHERE (time >= '$day 00:00+00' AND time < '$day 01:00+00' OR time >= '$day 12:00+00' AND time < '$day 13:00+00' OR time >= '$day 20:00+00' AND time < '$day 21:00+00') AND type IN ('host_cpu','host_cpu_hw') AND dev <> '' AND dev NOT LIKE 'j:%');" < /dev/null 2>/dev/null || echo "has_percore=unknown(timeout)")
      fi
      echo "$day|$comp|$size|$est|$c"
    done
}

# Stop the current run and start its days again (with the repair command
# copied fresh from this checkout). Windows already repaired are no-ops.
restart() {
  local q days
  q="$(current)/queue"; [ -s "$q" ] || q="$RUN/queue"
  [ -s "$q" ] || die "no run to restart (no queue in $RUN)"
  days=$(awk '{print $1}' "$q" | sort -u | tr '\n' ' ')
  echo "== restarting days: $days"
  stop
  start $days
}

# One status line for the current run: jobs finished, failed, running, and
# per-core rows collapsed so far (from the job logs' per-batch lines).
progress_line() {
  local dir; dir=$(current)
  local total=0 done_n=0 failed=0 rows=0
  if [ -n "$dir" ] && [ -f "$dir/queue" ]; then
    total=$(wc -l < "$dir/queue")
    done_n=$(grep -l '^rc=' "$dir"/logs/*.log 2>/dev/null | wc -l)
    failed=$(grep -l '^rc=[1-9]' "$dir"/logs/*.log 2>/dev/null | wc -l)
    rows=$(grep -h ' hosts=[0-9]* percore_rows=' "$dir"/logs/*.log 2>/dev/null \
      | sed 's/.* percore_rows=\([0-9]*\).*/\1/' | awk '{s += $1} END {print s + 0}')
  fi
  echo "== $(date -u +%FT%TZ) jobs finished=$done_n/$total failed=$failed running=$(repair_count) per-core rows collapsed=$rows"
}

# Wait until no runner of this script and no repair process is left, printing
# a status line every STATUS_S seconds.
wait_for_run() {
  local last=0 now
  while pgrep -f "xargs .*-L 1 $SELF _job" > /dev/null || [ "$(repair_count)" != "0" ]; do
    now=$(date +%s)
    if [ $((now - last)) -ge "$STATUS_S" ]; then progress_line; last=$now; fi
    sleep "$POLL_S"
  done
  progress_line
}

# Survey, then (re)start the repair on every day with per-core rows: an
# uncompressed day with any per-core share, a compressed day whose check found
# per-core rows or could not decide. Stops the current run first; days already
# repaired are no-ops. Days start skipped (the merge is writing them) are
# retried after each run until none is left; any other start failure ends auto.
# Returns once the last run has finished; prints a status line while waiting.
auto() {
  local out days rc
  out=$(survey "${1:-2026-09-15}") || die "survey failed"
  echo "$out"
  days=$(awk -F'|' '($2 == "f" && $5 ~ /^[0-9.]+$/ && $5 + 0 > 0) || ($2 == "t" && $5 ~ /^has_percore=(true|unknown)/) {print $1}' <<< "$out" | sort -u | tr '\n' ' ')
  if [ -z "${days// /}" ]; then echo "== no day with per-core rows; nothing to repair"; return 0; fi
  echo "== repairing every day with per-core rows: $days"
  stop
  while [ -n "${days// /}" ]; do
    out=$(start $days 2>&1); rc=$?
    echo "$out"
    if [ $rc -ne 0 ] && ! grep -q 'the merge is writing to every requested day' <<< "$out"; then
      die "start failed; auto stops (see the error above)"
    fi
    # start prints one SKIPPED line per day the merge holds (also when it then
    # gives up because the merge holds them all).
    days=$(sed -n 's/^SKIPPED \([0-9-]*\): the merge.*/\1/p' <<< "$out" | tr '\n' ' ')
    [ -n "${days// /}" ] || break
    echo "== $(date -u +%FT%TZ) waiting to retry days the merge holds: $days"
    wait_for_run
    sleep "$MERGE_RETRY_S"
  done
  wait_for_run
  echo "== $(date -u +%FT%TZ) auto done: every affected day has run"
  status
}

case "${1:-}" in
  _job) shift; job "$@" ;;
  stop) stop ;;
  start) shift; start "$@" ;;
  restart) restart ;;
  auto) shift; auto "$@" ;;
  resume-policy) resume_policy ;;
  status) status ;;
  survey) shift; survey "$@" ;;
  *) die "usage: $0 {auto [SINCE_DAY]|stop|start DAY [DAY ...]|restart|resume-policy|status|survey [SINCE_DAY]}" ;;
esac
