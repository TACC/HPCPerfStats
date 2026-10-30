#!/usr/bin/env bash
# migrate_daily_archive_gz_to_zst.sh - offline: convert legacy daily
# <day>.tar.gz archives to <day>.tar.zst. A .tar.gz is removed only after a
# verified replacement exists; on any error the day is left exactly as found.
#
# Run with the sync_timedb pipeline STOPPED. Dry run by default; --apply acts.
# In the pipeline container, as hpcperfstats (keeps owner/mode):
#   bash /home/hpcperfstats/scripts/migrate_daily_archive_gz_to_zst.sh [--apply]
#
# Per <day>.tar.gz (oldest first):
#   open <day>.tar present -> left alone; seal it first with
#     seal_open_daily_tars_to_zst.sh, then re-run this (the gz is then checked
#     against the new .tar.zst)
#   no .tar.zst -> convert: the gzip stream is decompressed with
#     zstd --format=gzip (CRC + length checked) and recompressed with
#     zstd -T0 -<level> into <day>.tar.zst.gz2zst.tmp while the uncompressed
#     bytes are hashed; the tmp is then decompressed again and must hash to
#     the same sha256 (byte-identical tar, duplicates and all). Then owner/mode
#     are copied, fsync, the tmp is hard-linked to <day>.tar.zst (fails if one
#     appeared) and only then is the .tar.gz removed
#   .tar.zst exists -> drop the gz only if every regular-file member of the gz
#     (name, size, sha256 of its bytes) is also in the zst; otherwise both are
#     kept and the missing members are logged (KEEP BOTH)
# A gz or zst that does not decompress cleanly is never removed (FAIL).
# The .tar.gz is re-checked (size, mtime, inode) right before it is removed.
# Only one run at a time (flock on <daily>/.migrate_daily_archive_gz_to_zst.lock).
set -uo pipefail

DAILY=/hpcperfstats/daily_archive
LEVEL=7
MARGIN_GB=200
JOBS=1
APPLY=0
ALLOW_RUNNING=0
ONLY=""
LOG=""

usage() {
  cat <<EOF
usage: $0 [--apply] [-j|--jobs N] [--daily DIR] [--level N] [--margin-gb N]
          [--only YYYY-MM-DD] [--allow-running] [--log FILE]
Dry run unless --apply. Defaults: daily=$DAILY level=$LEVEL
margin-gb=$MARGIN_GB jobs=$JOBS
--jobs N: convert up to N days at once; each running conversion reserves the
  .tar.gz size against free space minus the other reservations minus the
  --margin-gb floor (kept free once for the whole run). A day with no room
  waits and is retried after space is given back; it is skipped only when
  nothing running is left to free space.
The dry run reads nothing for gz-only days; for days that also have a
.tar.zst it reads both to decide whether the gz can be dropped.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --daily) DAILY=$2; shift ;;
    --level) LEVEL=$2; shift ;;
    --margin-gb) MARGIN_GB=$2; shift ;;
    -j|--jobs) JOBS=$2; shift ;;
    --only) ONLY=$2; shift ;;
    --allow-running) ALLOW_RUNNING=1 ;;
    --log) LOG=$2; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo "--jobs must be a positive integer" >&2; exit 2; }
for bin in zstd sha256sum mkfifo tee awk stat df python3 pgrep flock ln; do
  command -v "$bin" >/dev/null || { echo "missing $bin" >&2; exit 2; }
done
[ -d "$DAILY" ] || { echo "no daily dir $DAILY" >&2; exit 2; }
python3 -I -c 'import gzip, sys; sys.stdout.buffer.write(gzip.compress(b"x"))' |
  zstd -dc --format=gzip >/dev/null 2>&1 ||
  { echo "zstd lacks --format=gzip support" >&2; exit 2; }
if [ "$ALLOW_RUNNING" -eq 0 ] && pgrep -f 'sync_timedb\.py' >/dev/null; then
  echo "sync_timedb is running; stop the pipeline first (or --allow-running)" >&2
  exit 2
fi
exec 8>>"$DAILY/.migrate_daily_archive_gz_to_zst.lock" ||
  { echo "cannot open lock file in $DAILY" >&2; exit 2; }
flock -n 8 || { echo "another run of this script holds the lock; not starting" >&2; exit 2; }

LOG=${LOG:-$DAILY/migrate_daily_archive_gz_to_zst.$(date +%Y%m%d-%H%M%S).log}
log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
gib() { awk -v b="$1" 'BEGIN{printf "%.2f", b/2^30}'; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/migrate_gz_to_zst.XXXXXX") || exit 2
trap 'rm -rf "${WORK:?}"' EXIT
log "start apply=$APPLY jobs=$JOBS daily=$DAILY level=$LEVEL margin=${MARGIN_GB}GiB"

# Reads a tar stream on stdin; prints one sorted line per regular file:
# "<sha256>\t<size>\t<json name>". Exits non-zero on any tar error.
cat >"$WORK/member_hashes.py" <<'PY'
import hashlib
import json
import sys
import tarfile

rows = []
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|", bufsize=8 << 20) as tf:
  for m in tf:
    if not m.isreg():
      continue
    h = hashlib.sha256()
    n = 0
    fh = tf.extractfile(m)
    while True:
      chunk = fh.read(8 << 20)
      if not chunk:
        break
      h.update(chunk)
      n += len(chunk)
    if n != m.size:
      sys.exit("short member %s: %d of %d bytes" % (m.name, n, m.size))
    rows.append("%s\t%d\t%s" % (h.hexdigest(), m.size, json.dumps(m.name)))
for row in sorted(set(rows)):
  print(row)
PY

mkdir -p "$WORK/res" "$WORK/resv" "$WORK/day" "$WORK/freed"
freed_count() { find "$WORK/freed" -type f | wc -l; }
mark_freed() { : >"$WORK/freed/$1.$BASHPID.$RANDOM$RANDOM"; }

# Reserve $1 bytes for day $2 against free space minus other reservations minus
# the --margin-gb floor. On no room prints "<room> <freed count now>".
reserve() {
  (
    flock 9
    avail=$(df --output=avail -B1 "$DAILY" | tail -1)
    held=$(cat "$WORK"/resv/* 2>/dev/null | awk '{s += $1} END {print s + 0}')
    room=$((avail - held - MARGIN_GB * 2 ** 30))
    [ "$room" -ge "$1" ] || { echo "$room $(freed_count)"; exit 1; }
    echo "$1" >"$WORK/resv/$2"
  ) 9>"$WORK/resv.lock"
}

# One day. Writes "<status> <n>" to $WORK/res/<day>: status
# converted|dropped|skip|fail|space; n = bytes freed (gz size on a dry run) or,
# for space, the freed count when the day was turned away.
migrate_day() {
  local gz=$1 day zst tar tmp size W before src dst zsize st need room stamp
  day=$(basename "$gz" .tar.gz)
  zst="$DAILY/$day.tar.zst"
  tar="$DAILY/$day.tar"
  tmp="$zst.gz2zst.tmp"
  size=$(stat -c %s "$gz")
  W="$WORK/day/$day"
  mkdir -p "$W"
  result() { echo "$1 ${2:-0}" >"$WORK/res/$day"; }
  release() {
    if [ -f "$WORK/resv/$day" ]; then rm -f "$WORK/resv/$day"; mark_freed "$day"; fi
  }
  skip() { release; log "SKIP $day $(gib "$size")GiB: $*"; result skip; }
  fail() {
    release
    if [ -e "$tmp" ]; then rm -f "$tmp"; mark_freed "$day"; fi
    log "FAIL $day $(gib "$size")GiB: $* (.tar.gz kept)"; result fail
  }

  if [ -e "$tar" ]; then
    skip "open .tar present; seal it with seal_open_daily_tars_to_zst.sh, then re-run"; return
  fi

  if [ -f "$zst" ]; then
    # gz + zst: drop the gz only if every gz member is a byte copy in the zst.
    before=$(stat -c '%s %Y %i' "$gz")
    zstd -dc --format=gzip -- "$gz" 2>"$W/g.err" |
      python3 -I "$WORK/member_hashes.py" >"$W/g.lst" 2>>"$W/g.err"
    st=("${PIPESTATUS[@]}")
    if [ "${st[0]}" -ne 0 ] || [ "${st[1]}" -ne 0 ]; then
      fail "cannot read .tar.gz: $(head -1 "$W/g.err")"; return
    fi
    zstd -dc -- "$zst" 2>"$W/z.err" |
      python3 -I "$WORK/member_hashes.py" >"$W/z.lst" 2>>"$W/z.err"
    st=("${PIPESTATUS[@]}")
    if [ "${st[0]}" -ne 0 ] || [ "${st[1]}" -ne 0 ]; then
      fail "cannot read existing .tar.zst (both kept): $(head -1 "$W/z.err")"; return
    fi
    LC_ALL=C comm -23 "$W/g.lst" "$W/z.lst" >"$W/missing"
    if [ -s "$W/missing" ]; then
      log "KEEP BOTH $day: $(wc -l <"$W/missing") of $(wc -l <"$W/g.lst") .tar.gz members are not byte copies in the .tar.zst, e.g. $(head -3 "$W/missing" | cut -f2- | tr '\t\n' '  ')"
      result skip; return
    fi
    if [ "$APPLY" -eq 0 ]; then
      log "WOULD DROP $day $(gib "$size")GiB: all $(wc -l <"$W/g.lst") members are in the .tar.zst"
      result dropped "$size"; return
    fi
    if [ "$(stat -c '%s %Y %i' "$gz")" != "$before" ] || [ ! -f "$zst" ]; then
      fail "files changed while checking (is the pipeline running?)"; return
    fi
    rm -f -- "$gz"
    sync -f "$zst" 2>/dev/null || sync
    mark_freed "$day"
    log "DROPPED $day $(gib "$size")GiB: all $(wc -l <"$W/g.lst") members are in the .tar.zst"
    result dropped "$size"; return
  fi

  if [ "$APPLY" -eq 0 ]; then
    log "WOULD CONVERT $day $(gib "$size")GiB"
    result converted "$size"; return
  fi
  if [ -e "$tmp" ]; then
    log "removing leftover $tmp from an earlier run"
    rm -f -- "$tmp"; mark_freed "$day"
  fi
  need=$size
  if ! room=$(reserve "$need" "$day"); then
    read -r room stamp <<<"$room"
    log "WAIT $day $(gib "$size")GiB: $(gib "$room")GiB usable after other running days and the ${MARGIN_GB}GiB floor; retry when space is freed"
    result space "$stamp"; return
  fi

  before=$(stat -c '%s %Y %i' "$gz")
  log "CONVERT $day $(gib "$size")GiB"
  mkfifo "$W/fifo"
  sha256sum <"$W/fifo" >"$W/src.sha" &
  local shapid=$!
  zstd -dc --format=gzip -- "$gz" 2>"$W/d.err" | tee "$W/fifo" |
    zstd -T0 "-$LEVEL" -q -f -o "$tmp" 2>"$W/c.err"
  st=("${PIPESTATUS[@]}")
  wait "$shapid"
  local shrc=$?
  rm -f "$WORK/resv/$day"  # the tmp file now holds the space itself
  if [ "${st[0]}" -ne 0 ] || [ -s "$W/d.err" ]; then
    fail "gzip decompress (corrupt or truncated .tar.gz?): $(head -1 "$W/d.err")"; return
  fi
  if [ "${st[1]}" -ne 0 ] || [ "${st[2]}" -ne 0 ] || [ -s "$W/c.err" ] || [ "$shrc" -ne 0 ]; then
    fail "compress: $(head -1 "$W/c.err")"; return
  fi
  src=$(cut -d' ' -f1 "$W/src.sha")
  dst=$(zstd -dc -- "$tmp" 2>"$W/v.err" | sha256sum)
  st=("${PIPESTATUS[@]}")
  dst=${dst%% *}
  if [ "${st[0]}" -ne 0 ] || [ -s "$W/v.err" ] || [ "${#src}" -ne 64 ] || [ "$src" != "$dst" ]; then
    fail "verify: .tar.zst content differs from .tar.gz (gz=$src zst=$dst) $(head -1 "$W/v.err")"; return
  fi
  if [ "$(stat -c '%s %Y %i' "$gz")" != "$before" ] || [ -e "$tar" ]; then
    fail ".tar.gz or .tar changed during convert (is the pipeline running?)"; return
  fi
  chown --reference="$gz" -- "$tmp" 2>/dev/null
  chmod --reference="$gz" -- "$tmp"
  sync -f "$tmp" 2>/dev/null || sync
  if ! ln -- "$tmp" "$zst" 2>"$W/ln.err"; then
    fail "cannot place .tar.zst (did one appear?): $(head -1 "$W/ln.err")"; return
  fi
  rm -f -- "$tmp"
  sync -f "$zst" 2>/dev/null || sync
  if [ "$(stat -c %s "$zst")" -le 0 ]; then
    fail ".tar.zst empty after placing it"; return
  fi
  rm -f -- "$gz"
  mark_freed "$day"
  zsize=$(stat -c %s "$zst")
  log "DONE $day gz=$(gib "$size")GiB zst=$(gib "$zsize")GiB sha256=$src"
  result converted "$((size - zsize))"
  rm -rf "${W:?}"
}

running=0 FREED=0
RETRY=()
declare -A STAMP=()

start_day() { migrate_day "$1" & running=$((running + 1)); }

collect() {
  local f st n day
  for f in "$WORK"/res/*; do
    [ -e "$f" ] || continue
    read -r st n <"$f"
    [ "$st" = space ] || continue
    day=${f##*/}
    STAMP[$day]=$n
    RETRY+=("$day")
    rm -f "$f"
  done
  FREED=$(freed_count)
}

start_retries() {
  local keep=() day
  for day in $(printf '%s\n' "${RETRY[@]}" | sort); do
    if [ "$running" -lt "$JOBS" ] && [ "$FREED" -gt "${STAMP[$day]}" ]; then
      log "RETRY $day: space freed since it waited"
      start_day "$DAILY/$day.tar.gz"
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

for gz in "$DAILY"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].tar.gz; do
  [ -f "$gz" ] || continue
  day=$(basename "$gz" .tar.gz)
  [ -n "$ONLY" ] && [ "$day" != "$ONLY" ] && continue
  while [ "$running" -ge "$JOBS" ]; do reap; done
  start_day "$gz"
done
while [ "$running" -gt 0 ]; do reap; done
collect
start_retries
while [ "$running" -gt 0 ]; do reap; done
for day in "${RETRY[@]}"; do
  log "SKIP $day: not enough free space even after every other day finished (.tar.gz kept)"
  echo "skip 0" >"$WORK/res/$day"
done

read -r n_conv n_drop n_skip n_fail freed <<<"$(cat "$WORK"/res/* 2>/dev/null |
  awk '{c[$1]++; b += $2} END {printf "%d %d %d %d %d\n",
    c["converted"], c["dropped"], c["skip"], c["fail"], b}')"
if [ "$APPLY" -eq 1 ]; then
  log "end converted=$n_conv dropped=$n_drop skipped=$n_skip failed=$n_fail freed=$(gib "$freed")GiB"
else
  log "end (dry run) would_convert=$n_conv would_drop=$n_drop skipped=$n_skip failed=$n_fail gz_GiB=$(gib "$freed")"
fi
log "log: $LOG"
[ "$n_fail" -eq 0 ]
