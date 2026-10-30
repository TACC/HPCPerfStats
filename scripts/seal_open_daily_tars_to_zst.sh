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
# Kept open: days with append jobs queued in the job store snapshot
# (<archive>/.sync_timedb_job_store.json, lists.append + pending.append), plus
# any --keep-open DAY. Appends that were in flight when the pipeline stopped
# are not in the snapshot; pass those days with --keep-open.
#
# Per open tar <day>.tar (oldest first) it:
#   1. skips days younger than --min-age-days (still being written)
#   2. skips kept-open days
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
APPLY=0
ALLOW_RUNNING=0
NO_JOB_STORE=0
ONLY=""
LOG=""
KEEP_OPEN=()

usage() {
  cat <<EOF
usage: $0 [--apply] [--keep-open YYYY-MM-DD]... [--daily DIR] [--archive DIR]
          [--tz TZ] [--level N] [--min-age-days N] [--margin-gb N]
          [--only YYYY-MM-DD] [--no-job-store] [--allow-running] [--log FILE]
Dry run unless --apply. Defaults: daily=$DAILY archive=$ARCHIVE tz=$TZ_LOCAL
level=$LEVEL min-age-days=$MIN_AGE_DAYS margin-gb=$MARGIN_GB
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

for bin in tar zstd awk stat df python3 pgrep; do
  command -v "$bin" >/dev/null || { echo "missing $bin" >&2; exit 2; }
done
tar --version | head -1 | grep -q 'GNU tar' || { echo "need GNU tar" >&2; exit 2; }
[ -d "$DAILY" ] || { echo "no daily dir $DAILY" >&2; exit 2; }
if [ "$ALLOW_RUNNING" -eq 0 ] && pgrep -f 'sync_timedb\.py' >/dev/null; then
  echo "sync_timedb is running; stop the pipeline first (or --allow-running)" >&2
  exit 2
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/seal_open_daily_tars.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
CUTOFF=$(TZ=$TZ_LOCAL date -d "today - $MIN_AGE_DAYS days" +%F)
log "start apply=$APPLY daily=$DAILY archive=$ARCHIVE cutoff<$CUTOFF level=$LEVEL"

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

# Raw index for the report: "<day> <host>/<file> <size>".
find "$ARCHIVE" -mindepth 2 -maxdepth 2 -type f -regex '.*/[0-9]+' \
  -printf '%s %p\n' 2>/dev/null |
  TZ=$TZ_LOCAL python3 -I -c '
import sys, time
time.tzset()
for line in sys.stdin:
  size, path = line.rstrip("\n").split(" ", 1)
  parts = path.split("/")
  day = time.strftime("%Y-%m-%d", time.localtime(int(parts[-1])))
  print(day, parts[-2] + "/" + parts[-1], size)
' >"$WORK/raw.idx"
log "raw files on disk: $(wc -l <"$WORK/raw.idx")"

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

n_done=0 n_skip=0 n_fail=0 n_restore=0 freed=0
for tar in "$DAILY"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].tar; do
  [ -f "$tar" ] || continue
  day=$(basename "$tar" .tar)
  [ -n "$ONLY" ] && [ "$day" != "$ONLY" ] && continue
  zst="$tar.zst"
  size=$(stat -c %s "$tar")
  skip() { log "SKIP $day $(gib "$size")GiB: $*"; n_skip=$((n_skip + 1)); }

  if [[ ! "$day" < "$CUTOFF" ]]; then
    skip "younger than $MIN_AGE_DAYS days"; continue
  fi
  if grep -q "^$day " "$WORK/keep"; then
    skip "kept open (append queued)"; continue
  fi
  if [ "$size" -le 20480 ]; then
    skip "empty tar (pipeline drops it)"; continue
  fi

  if ! tar -tvf "$tar" >"$WORK/t.raw" 2>"$WORK/t.err" || [ -s "$WORK/t.err" ]; then
    skip "tar listing failed, pipeline repairs it: $(head -1 "$WORK/t.err")"; continue
  fi
  members <"$WORK/t.raw" >"$WORK/t.all"
  dups=$(cut -f1 "$WORK/t.all" | uniq -d | wc -l)
  dedupe_list <"$WORK/t.all" >"$WORK/t.lst"

  if [ -f "$zst" ]; then
    if ! zstd -dc "$zst" 2>"$WORK/z.err" | tar -tvf - >"$WORK/z.raw" 2>>"$WORK/z.err" ||
      [ -s "$WORK/z.err" ]; then
      skip "existing .tar.zst unreadable: $(head -1 "$WORK/z.err")"; continue
    fi
    members <"$WORK/z.raw" >"$WORK/z.lst"
    missing=$(awk -F'\t' 'NR == FNR {t[$1] = $2; next}
      !($1 in t) || t[$1] + 0 < $2 + 0 {m++} END {print m + 0}' \
      "$WORK/t.lst" "$WORK/z.lst")
    if [ "$missing" -gt 0 ]; then
      skip "existing .tar.zst has $missing members the tar lacks or has smaller (pipeline reconcile)"
      continue
    fi
  fi

  avail=$(df --output=avail -B1 "$DAILY" | tail -1)
  need=$((size / 3 + MARGIN_GB * 2 ** 30))
  if [ "$avail" -lt "$need" ]; then
    skip "only $(gib "$avail")GiB free, need $(gib "$need")GiB for the compressed copy"
    continue
  fi

  pending=$(awk -v d="$day" 'NR == FNR {split($0, f, "\t");
      n = split(f[1], parts, "/"); t[parts[n - 1] "/" parts[n]] = f[2]; next}
    $1 == d && (!($2 in t) || t[$2] != $3) {p++} END {print p + 0}' \
    "$WORK/t.lst" "$WORK/raw.idx")
  note="members=$(wc -l <"$WORK/t.lst") raw_not_in_tar=$pending"
  [ "$dups" -gt 0 ] && note="$note dedupe_names=$dups"
  [ -f "$zst" ] && note="$note replaces_existing_zst"
  [ "$pending" -gt 0 ] && n_restore=$((n_restore + 1))

  if [ "$APPLY" -eq 0 ]; then
    log "WOULD SEAL $day $(gib "$size")GiB $note"
    n_done=$((n_done + 1)); freed=$((freed + size)); continue
  fi

  tmp="$tar.zst.tmp"
  before=$(stat -c '%s %Y' "$tar")
  log "SEAL $day $(gib "$size")GiB $note"
  if [ "$dups" -gt 0 ]; then
    python3 -I "$WORK/dedupe_stream.py" "$tar" 2>"$WORK/c.err" |
      zstd -T0 "-$LEVEL" -q -f -o "$tmp" 2>>"$WORK/c.err"
  else
    zstd -T0 "-$LEVEL" -q -f -o "$tmp" "$tar" 2>"$WORK/c.err"
  fi
  if [ $? -ne 0 ] || [ -s "$WORK/c.err" ]; then
    log "FAIL $day compress: $(head -1 "$WORK/c.err")"; rm -f "$tmp"
    n_fail=$((n_fail + 1)); continue
  fi
  if ! zstd -dc "$tmp" 2>"$WORK/v.err" | tar -tvf - 2>>"$WORK/v.err" | members >"$WORK/v.lst" ||
    [ -s "$WORK/v.err" ] || ! cmp -s "$WORK/t.lst" "$WORK/v.lst"; then
    log "FAIL $day verify: member list or checksum mismatch $(head -1 "$WORK/v.err")"
    rm -f "$tmp"; n_fail=$((n_fail + 1)); continue
  fi
  if [ "$(stat -c '%s %Y' "$tar")" != "$before" ]; then
    log "FAIL $day tar changed during compress (is the pipeline running?)"
    rm -f "$tmp"; n_fail=$((n_fail + 1)); continue
  fi
  chown --reference="$tar" "$tmp" && chmod --reference="$tar" "$tmp"
  sync -f "$tmp" 2>/dev/null || sync
  mv -f "$tmp" "$zst"
  sync -f "$zst" 2>/dev/null || sync
  rm -f "$tar"
  zsize=$(stat -c %s "$zst")
  freed=$((freed + size - zsize))
  n_done=$((n_done + 1))
  log "DONE $day tar=$(gib "$size")GiB zst=$(gib "$zsize")GiB"
done

if [ "$APPLY" -eq 1 ]; then
  log "end sealed=$n_done skipped=$n_skip failed=$n_fail freed=$(gib "$freed")GiB days_needing_restore_later=$n_restore"
else
  log "end (dry run) would_seal=$n_done skipped=$n_skip tar_GiB_to_compress=$(gib "$freed") days_needing_restore_later=$n_restore"
fi
log "log: $LOG"
[ "$n_fail" -eq 0 ]
