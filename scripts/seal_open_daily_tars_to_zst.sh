#!/usr/bin/env bash
# seal_open_daily_tars_to_zst.sh - offline: compress verified open daily tars
# to .tar.zst and remove the .tar. Only days the pipeline is about to append to
# stay open; every other day stays sealed until the pipeline needs it (it
# restores the tar from the .tar.zst on its own when an append arrives).
#
# Run with the sync_timedb pipeline STOPPED. Dry run by default; --apply acts.
# In the pipeline container:
#   bash /home/hpcperfstats/scripts/seal_open_daily_tars_to_zst.sh [--apply]
#
# Kept open (detected, no flags needed): a day with a raw file that is already
# ingested (file-complete or zero-host mark, keyed path|mtime|size) but not a
# same-size member of its tar, i.e. its append is queued, was in flight, or was
# cut off by the stop; and a day with append jobs in the job store snapshot
# (<archive>/.sync_timedb_job_store.json). --keep-open DAY adds days by hand.
#
# Per open tar <day>.tar (oldest first) it:
#   1. skips days younger than --min-age-days (still being written)
#   2. skips kept-open days (snapshot / --keep-open, then ingested raw not in
#      the tar once the tar is listed)
#   3. lists the tar with GNU tar; any error or warning = corrupt, skipped
#      (the pipeline repairs it on restart)
#   4. with duplicate member names, dedupes while compressing: the tar is
#      streamed through a filter that keeps one copy per name (largest size,
#      the pipeline's dedupe rule; on a tie the last copy, which extraction
#      would return) straight into zstd; the original tar is never modified
#   5. with an existing <day>.tar.zst: requires every zst member to be in the
#      (deduped) tar at the same or larger size, else skipped (pipeline
#      reconcile)
#   6. compresses to <day>.tar.zst.tmp (zstd -T0 -<level>, the pipeline's flags)
#   7. decompresses the tmp and compares the full member list (name + size)
#      with the tar's (deduped) list; any difference or zstd checksum error
#      aborts that day
#   8. copies owner/mode, fsyncs, renames over <day>.tar.zst, removes <day>.tar
# It reports how many raw files of each sealed day are not in the tar yet
# (each such day is restored once when the pipeline appends them).
# POSIX awk only (the pipeline image ships mawk); day math runs in python3.
set -uo pipefail

DAILY=/hpcperfstats/daily_archive
ARCHIVE=/hpcperfstats/archive
TZ_LOCAL=America/Chicago
LEVEL=7
MIN_AGE_DAYS=3
MARGIN_GB=200
JOBS=1
APPLY=0
ALLOW_RUNNING=0
NO_JOB_STORE=0
ONLY=""
LOG=""
KEEP_OPEN=()

usage() {
  cat <<EOF
usage: $0 [--apply] [-j|--jobs N] [--keep-open YYYY-MM-DD]... [--daily DIR]
          [--archive DIR] [--tz TZ] [--level N] [--min-age-days N]
          [--margin-gb N] [--only YYYY-MM-DD] [--no-job-store]
          [--allow-running] [--log FILE]
Dry run unless --apply. Defaults: daily=$DAILY archive=$ARCHIVE tz=$TZ_LOCAL
level=$LEVEL min-age-days=$MIN_AGE_DAYS margin-gb=$MARGIN_GB jobs=$JOBS
--jobs N: seal up to N days at once (more reads in flight on a SAN); each
  running day reserves its estimated compressed size against free space.
  A day with no room waits (WAIT) and is retried (RETRY) after a later day
  finishes; it is skipped only when no running day is left to free space.
--no-job-store: do not read the job store snapshot (only --keep-open stays open)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --keep-open) KEEP_OPEN+=("$2"); shift ;;
    --daily) DAILY=$2; shift ;;
    --archive) ARCHIVE=$2; shift ;;
    --tz) TZ_LOCAL=$2; shift ;;
    --level) LEVEL=$2; shift ;;
    --min-age-days) MIN_AGE_DAYS=$2; shift ;;
    --margin-gb) MARGIN_GB=$2; shift ;;
    -j|--jobs) JOBS=$2; shift ;;
    --only) ONLY=$2; shift ;;
    --no-job-store) NO_JOB_STORE=1 ;;
    --allow-running) ALLOW_RUNNING=1 ;;
    --log) LOG=$2; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

LOG=${LOG:-$DAILY/seal_open_daily_tars.$(date +%Y%m%d-%H%M%S).log}
log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
gib() { awk -v b="$1" 'BEGIN{printf "%.1f", b/2^30}'; }

[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo "--jobs must be a positive integer" >&2; exit 2; }
for bin in tar zstd awk stat df python3 pgrep flock; do
  command -v "$bin" >/dev/null || { echo "missing $bin" >&2; exit 2; }
done
tar --version | head -1 | grep -q 'GNU tar' || { echo "need GNU tar" >&2; exit 2; }
[ -d "$DAILY" ] || { echo "no daily dir $DAILY" >&2; exit 2; }
if [ "$ALLOW_RUNNING" -eq 0 ] && pgrep -f 'sync_timedb\.py' >/dev/null; then
  echo "sync_timedb is running; stop the pipeline first (or --allow-running)" >&2
  exit 2
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/seal_open_daily_tars.XXXXXX") || exit 2
trap 'rm -rf "${WORK:?}"' EXIT
CUTOFF=$(TZ=$TZ_LOCAL date -d "today - $MIN_AGE_DAYS days" +%F)
log "start apply=$APPLY jobs=$JOBS daily=$DAILY archive=$ARCHIVE cutoff<$CUTOFF level=$LEVEL"

# Days kept open: "<day> <queued append files>".
: >"$WORK/keep"
if [ "$NO_JOB_STORE" -eq 0 ]; then
  SNAP="$ARCHIVE/.sync_timedb_job_store.json"
  [ -f "$SNAP" ] || { log "no job store snapshot $SNAP (use --no-job-store)"; exit 2; }
  if ! TZ=$TZ_LOCAL python3 -I - "$SNAP" >"$WORK/keep" 2>"$WORK/keep.err" <<'PY'
import json, os, sys, time

time.tzset()
with open(sys.argv[1]) as fh:
  doc = json.load(fh)


def find(node):
  if isinstance(node, dict):
    if "lists" in node:
      return node
    for value in node.values():
      hit = find(value)
      if hit is not None:
        return hit
  return None


snap = find(doc) or {}
counts = {}
for src in (
  (snap.get("lists") or {}).get("append") or [],
  (snap.get("pending") or {}).get("append") or [],
):
  for path in src:
    name = os.path.basename(str(path))
    if name.isdigit():
      day = time.strftime("%Y-%m-%d", time.localtime(int(name)))
      counts[day] = counts.get(day, 0) + 1
for day in sorted(counts):
  print(day, counts[day])
PY
  then
    log "cannot read job store snapshot: $(head -1 "$WORK/keep.err")"; exit 2
  fi
fi
for day in "${KEEP_OPEN[@]}"; do echo "$day manual" >>"$WORK/keep"; done
while read -r day n; do log "KEEP OPEN $day (append queued: $n)"; done <"$WORK/keep"

# Raw index: "<day> <host>/<file> <size> <ingested 0|1>". Ingested means a
# file-complete or zero-host mark for the file's path|mtime|size.
find "$ARCHIVE" -mindepth 2 -maxdepth 2 -type f -regex '.*/[0-9]+' \
  -printf '%s %T@ %p\n' 2>/dev/null |
  TZ=$TZ_LOCAL python3 -I -c '
import json, os, sys, time

time.tzset()
archive = sys.argv[1]
marked = set()
for name in (".sync_timedb_file_complete_ingest_mark.json",
             ".sync_timedb_zero_host_ingest_mark.json"):
  try:
    with open(os.path.join(archive, name)) as fh:
      doc = json.load(fh)
  except (OSError, ValueError):
    continue
  stack = [doc]
  while stack:
    node = stack.pop()
    if isinstance(node, dict):
      entries = node.get("entries")
      if isinstance(entries, dict):
        marked.update(entries)
      else:
        stack.extend(node.values())
for line in sys.stdin:
  size, mtime, path = line.rstrip("\n").split(" ", 2)
  parts = path.split("/")
  day = time.strftime("%Y-%m-%d", time.localtime(int(parts[-1])))
  key = "%s|%d|%s" % (os.path.normpath(path), int(float(mtime)), size)
  print(day, parts[-2] + "/" + parts[-1], size, int(key in marked))
' "$ARCHIVE" >"$WORK/raw.idx"
log "raw files on disk: $(wc -l <"$WORK/raw.idx") ingested: $(awk '$4 == 1' "$WORK/raw.idx" | wc -l)"

# "<name>\t<size>" sorted, from a GNU tar -tv listing on stdin.
members() { awk '{print $6 "\t" $3}' | LC_ALL=C sort; }
# The same list with one entry per name, the largest size.
dedupe_list() {
  awk -F'\t' '!($1 in s) || $2 + 0 > s[$1] + 0 {s[$1] = $2}
    END {for (n in s) print n "\t" s[n]}' | LC_ALL=C sort
}

# Stream a tar to stdout keeping one copy per regular-file name: the largest,
# the last of equal-size copies. Other entries pass through unchanged.
cat >"$WORK/dedupe_stream.py" <<'PY'
import sys
import tarfile

src = sys.argv[1]
with tarfile.open(src, "r:") as tin:
  members = tin.getmembers()
  keep = {}
  for idx, m in enumerate(members):
    if m.isreg():
      best = keep.get(m.name)
      if best is None or m.size >= members[best].size:
        keep[m.name] = idx
  out = tarfile.open(fileobj=sys.stdout.buffer, mode="w|",
                     format=tarfile.PAX_FORMAT)
  out.copybufsize = 8 << 20
  for idx, m in enumerate(members):
    if m.isreg():
      if keep[m.name] != idx:
        continue
      with tin.extractfile(m) as fh:
        out.addfile(m, fh)
    else:
      out.addfile(m)
  out.close()
PY

mkdir -p "$WORK/res" "$WORK/resv" "$WORK/day"

# Space for the compressed copies of all running days: reserve under a lock,
# against free space minus what other running days already reserved.
reserve() {
  (
    flock 9
    avail=$(df --output=avail -B1 "$DAILY" | tail -1)
    held=$(cat "$WORK"/resv/* 2>/dev/null | awk '{s += $1} END {print s + 0}')
    [ $((avail - held)) -ge "$1" ] || { echo $((avail - held)); exit 1; }
    echo "$1" >"$WORK/resv/$2"
  ) 9>"$WORK/resv.lock"
}

# One day: verify, compress, check, swap. Writes "<status> <bytes> <restore>"
# to $WORK/res/<day> (status done|skip|fail|space; bytes freed, or tar size on
# a dry run; restore 1 when raw not yet ingested is left outside the tar).
# "space" means no room right now; the dispatcher retries the day after a
# later day finishes and frees space.
seal_day() {
  local tar=$1 day zst size W ready pending dups missing need room note
  local tmp="" before zsize rc
  day=$(basename "$tar" .tar)
  zst="$tar.zst"
  size=$(stat -c %s "$tar")
  W="$WORK/day/$day"
  mkdir -p "$W"
  result() { echo "$1 ${2:-0} ${3:-0}" >"$WORK/res/$day"; }
  skip() { rm -f "$WORK/resv/$day"; log "SKIP $day $(gib "$size")GiB: $*"; result skip; }
  fail() { rm -f "$WORK/resv/$day" "$tmp"; log "FAIL $day $*"; result fail; }

  if [ "$size" -le 20480 ]; then
    skip "empty tar (pipeline drops it)"; return
  fi
  if ! tar -tvf "$tar" >"$W/t.raw" 2>"$W/t.err" || [ -s "$W/t.err" ]; then
    skip "tar listing failed, pipeline repairs it: $(head -1 "$W/t.err")"; return
  fi
  members <"$W/t.raw" >"$W/t.all"
  dups=$(cut -f1 "$W/t.all" | uniq -d | wc -l)
  dedupe_list <"$W/t.all" >"$W/t.lst"

  # "<ingested raw not in tar> <not-yet-ingested raw not in tar>"
  read -r ready pending <<<"$(awk -v d="$day" 'NR == FNR {split($0, f, "\t");
      n = split(f[1], parts, "/"); t[parts[n - 1] "/" parts[n]] = f[2]; next}
    $1 == d && (!($2 in t) || t[$2] != $3) {if ($4 == 1) r++; else p++}
    END {print r + 0, p + 0}' "$W/t.lst" "$WORK/raw.idx")"
  if [ "$ready" -gt 0 ]; then
    skip "kept open: $ready ingested raw files waiting for append"; return
  fi

  # Reserve before the costly .tar.zst listing, so a day waiting for space
  # does not decompress its old .tar.zst on every retry.
  if [ "$APPLY" -eq 1 ]; then
    need=$((size / 3 + MARGIN_GB * 2 ** 30))
    if ! room=$(reserve "$need" "$day"); then
      log "WAIT $day $(gib "$size")GiB: only $(gib "$room")GiB free after other running days, need $(gib "$need")GiB; retry after the next day finishes"
      result space; return
    fi
  fi

  if [ -f "$zst" ]; then
    if ! zstd -dc "$zst" 2>"$W/z.err" | tar -tvf - >"$W/z.raw" 2>>"$W/z.err" ||
      [ -s "$W/z.err" ]; then
      skip "existing .tar.zst unreadable: $(head -1 "$W/z.err")"; return
    fi
    members <"$W/z.raw" >"$W/z.lst"
    missing=$(awk -F'\t' 'NR == FNR {t[$1] = $2; next}
      !($1 in t) || t[$1] + 0 < $2 + 0 {m++} END {print m + 0}' \
      "$W/t.lst" "$W/z.lst")
    if [ "$missing" -gt 0 ]; then
      skip "existing .tar.zst has $missing members the tar lacks or has smaller (pipeline reconcile)"
      return
    fi
  fi

  note="members=$(wc -l <"$W/t.lst") raw_not_in_tar=$pending"
  [ "$dups" -gt 0 ] && note="$note dedupe_names=$dups"
  [ -f "$zst" ] && note="$note replaces_existing_zst"
  if [ "$APPLY" -eq 0 ]; then
    log "WOULD SEAL $day $(gib "$size")GiB $note"
    result done "$size" "$((pending > 0))"; return
  fi

  tmp="$tar.zst.tmp"
  before=$(stat -c '%s %Y' "$tar")
  log "SEAL $day $(gib "$size")GiB $note"
  if [ "$dups" -gt 0 ]; then
    python3 -I "$WORK/dedupe_stream.py" "$tar" 2>"$W/c.err" |
      zstd -T0 "-$LEVEL" -q -f -o "$tmp" 2>>"$W/c.err"
  else
    zstd -T0 "-$LEVEL" -q -f -o "$tmp" "$tar" 2>"$W/c.err"
  fi
  rc=$?
  rm -f "$WORK/resv/$day"  # the tmp file now holds the space itself
  if [ "$rc" -ne 0 ] || [ -s "$W/c.err" ]; then
    fail "compress: $(head -1 "$W/c.err")"; return
  fi
  if ! zstd -dc "$tmp" 2>"$W/v.err" | tar -tvf - 2>>"$W/v.err" | members >"$W/v.lst" ||
    [ -s "$W/v.err" ] || ! cmp -s "$W/t.lst" "$W/v.lst"; then
    fail "verify: member list or checksum mismatch $(head -1 "$W/v.err")"; return
  fi
  if [ "$(stat -c '%s %Y' "$tar")" != "$before" ]; then
    fail "tar changed during compress (is the pipeline running?)"; return
  fi
  chown --reference="$tar" "$tmp" && chmod --reference="$tar" "$tmp"
  sync -f "$tmp" 2>/dev/null || sync
  mv -f "$tmp" "$zst"
  sync -f "$zst" 2>/dev/null || sync
  rm -f "$tar"
  zsize=$(stat -c %s "$zst")
  log "DONE $day tar=$(gib "$size")GiB zst=$(gib "$zsize")GiB"
  result done "$((size - zsize))" "$((pending > 0))"
  rm -rf "${W:?}"
}

# Days that found no room wait in RETRY with the count of finished days at the
# time they were turned away (STAMP); each is retried, oldest first, once a
# later finished day may have freed space (FINISHED > STAMP).
running=0 FINISHED=0
RETRY=()
declare -A STAMP=()

start_day() { seal_day "$1" & running=$((running + 1)); }

collect() {
  local f st day
  for f in "$WORK"/res/*; do
    [ -e "$f" ] || continue
    read -r st _ <"$f"
    [ "$st" = space ] || continue
    day=${f##*/}
    STAMP[$day]=$FINISHED
    RETRY+=("$day")
    rm -f "$f"
  done
  # Any finished day released its reservation (done also freed its tar).
  FINISHED=$(find "$WORK/res" -type f | wc -l)
}

start_retries() {
  local keep=() day
  for day in $(printf '%s\n' "${RETRY[@]}" | sort); do
    if [ "$running" -lt "$JOBS" ] && [ "$FINISHED" -gt "${STAMP[$day]}" ]; then
      log "RETRY $day: space freed since it waited"
      start_day "$DAILY/$day.tar"
    else
      keep+=("$day")
    fi
  done
  RETRY=("${keep[@]}")
}

reap() {
  wait -n
  running=$((running - 1))
  collect
  start_retries
}

for tar in "$DAILY"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].tar; do
  [ -f "$tar" ] || continue
  day=$(basename "$tar" .tar)
  [ -n "$ONLY" ] && [ "$day" != "$ONLY" ] && continue
  if [[ ! "$day" < "$CUTOFF" ]]; then
    log "SKIP $day: younger than $MIN_AGE_DAYS days"; echo "skip 0 0" >"$WORK/res/$day"
    continue
  fi
  if grep -q "^$day " "$WORK/keep"; then
    log "SKIP $day: kept open (append queued)"; echo "skip 0 0" >"$WORK/res/$day"
    continue
  fi
  while [ "$running" -ge "$JOBS" ]; do reap; done
  start_day "$tar"
done
while [ "$running" -gt 0 ]; do reap; done
collect
start_retries
while [ "$running" -gt 0 ]; do reap; done
for day in "${RETRY[@]}"; do
  log "SKIP $day: not enough free space even after every other day finished"
  echo "skip 0 0" >"$WORK/res/$day"
done

read -r n_done n_skip n_fail n_restore freed <<<"$(cat "$WORK"/res/* 2>/dev/null |
  awk '{c[$1]++; b += $2; r += $3} END {printf "%d %d %d %d %d\n",
    c["done"], c["skip"], c["fail"], r, b}')"
if [ "$APPLY" -eq 1 ]; then
  log "end sealed=$n_done skipped=$n_skip failed=$n_fail freed=$(gib "$freed")GiB days_needing_restore_later=$n_restore"
else
  log "end (dry run) would_seal=$n_done skipped=$n_skip tar_GiB_to_compress=$(gib "$freed") days_needing_restore_later=$n_restore"
fi
log "log: $LOG"
[ "$n_fail" -eq 0 ]
