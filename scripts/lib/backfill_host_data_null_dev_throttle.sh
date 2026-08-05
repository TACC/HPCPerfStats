# Adaptive concurrency helpers for scripts/backfill_host_data_null_dev.sh
# Sourced by the operator script; safe to source in unit tests (no side effects).
#
# Pressure signals (any trip ⇒ back off one worker):
#   - streaming replication lag (seconds; 0 when no standbys)
#   - uncheckpointed WAL bytes (current LSN − redo_lsn) vs max_wal_size * WAL_MULT
#   - PGDATA free bytes
# Latency degrade: chunk UPDATE duration >> EWMA baseline ⇒ back off
# Healthy streak at current target ⇒ ramp toward max (finds ceiling before degrade)
#
# WAL_MULT (env HPCPERFSTATS_NULL_DEV_WAL_FRAC) is a multiple of max_wal_size,
# not a 0–1 fraction. Bulk UPDATE routinely exceeds 1× max_wal_size (Postgres
# starts WAL checkpoints around that soft target). Default 5.0 ≈ allow up to
# 500% of max_wal_size uncheckpointed before backing off.
# Do not confuse with checkpoint log "wrote N buffers (P%)" — that P% is
# shared_buffers written, not WAL fill.

# null_dev_wal_pct_of_max CHECKPOINT_WAL_BYTES MAX_WAL_BYTES
# Integer percent of max_wal_size (may exceed 100). Unknown → -1.
null_dev_wal_pct_of_max() {
  local wal_bytes="${1:--1}"
  local max_wal_bytes="${2:--1}"
  if ! [[ "$wal_bytes" =~ ^[0-9]+$ && "$max_wal_bytes" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' '-1'
    return 0
  fi
  awk -v w="$wal_bytes" -v m="$max_wal_bytes" 'BEGIN { printf "%d", (100.0 * w) / m }'
}

# null_dev_eval_pressure LAG_SEC LAG_LIMIT CHECKPOINT_WAL_BYTES MAX_WAL_BYTES WAL_MULT
#   DISK_AVAIL_BYTES DISK_MIN_BYTES
# Prints 1 if under pressure, else 0. Unknown metrics (-1) are ignored.
# CHECKPOINT_WAL_BYTES is pg_wal_lsn_diff(pg_current_wal_lsn(), redo_lsn).
# WAL_MULT is multiples of max_wal_size (default 5.0).
null_dev_eval_pressure() {
  local lag_sec="${1:-0}"
  local lag_limit="${2:-30}"
  local wal_bytes="${3:--1}"
  local max_wal_bytes="${4:--1}"
  local wal_frac="${5:-5.0}"
  local disk_avail="${6:--1}"
  local disk_min="${7:--1}"

  if [[ "$lag_sec" =~ ^[0-9]+$ && "$lag_limit" =~ ^[0-9]+$ && "$lag_sec" -gt "$lag_limit" ]]; then
    printf '1\n'
    return 0
  fi

  if [[ "$wal_bytes" =~ ^[0-9]+$ && "$max_wal_bytes" =~ ^[1-9][0-9]*$ ]]; then
    # Pressure when uncheckpointed WAL > WAL_MULT * max_wal_size.
    local wal_cap
    wal_cap="$(awk -v m="$max_wal_bytes" -v f="$wal_frac" 'BEGIN { printf "%d", m * f }')"
    if [[ "$wal_cap" =~ ^[0-9]+$ && "$wal_bytes" -gt "$wal_cap" ]]; then
      printf '1\n'
      return 0
    fi
  fi

  if [[ "$disk_avail" =~ ^[0-9]+$ && "$disk_min" =~ ^[0-9]+$ && "$disk_min" -gt 0 && "$disk_avail" -lt "$disk_min" ]]; then
    printf '1\n'
    return 0
  fi

  printf '0\n'
}

# null_dev_adjust_concurrency CUR MAX MIN PRESSURE HEALTHY_STREAK
#   DURATION_MS BASELINE_MS [LATENCY_RATIO] [HEALTHY_NEEDED]
# Prints next target concurrency.
null_dev_adjust_concurrency() {
  local cur="${1:?}"
  local max="${2:?}"
  local min="${3:?}"
  local pressure="${4:-0}"
  local healthy="${5:-0}"
  local duration_ms="${6:-0}"
  local baseline_ms="${7:-0}"
  local latency_ratio="${8:-2.0}"
  local healthy_needed="${9:-3}"
  local next="$cur"

  if ! [[ "$cur" =~ ^[0-9]+$ && "$max" =~ ^[1-9][0-9]*$ && "$min" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "$cur"
    return 0
  fi
  if [[ "$min" -gt "$max" ]]; then
    min="$max"
  fi
  if [[ "$cur" -lt "$min" ]]; then
    cur="$min"
  fi
  if [[ "$cur" -gt "$max" ]]; then
    cur="$max"
  fi
  next="$cur"

  if [[ "$pressure" == "1" ]]; then
    next=$((cur > min ? cur - 1 : min))
    printf '%s\n' "$next"
    return 0
  fi

  if [[ "$duration_ms" =~ ^[1-9][0-9]*$ && "$baseline_ms" =~ ^[1-9][0-9]*$ ]]; then
    local limit_ms
    limit_ms="$(awk -v b="$baseline_ms" -v r="$latency_ratio" 'BEGIN { printf "%d", b * r }')"
    if [[ "$limit_ms" =~ ^[0-9]+$ && "$duration_ms" -gt "$limit_ms" ]]; then
      next=$((cur > min ? cur - 1 : min))
      printf '%s\n' "$next"
      return 0
    fi
  fi

  if [[ "$healthy" =~ ^[0-9]+$ && "$healthy_needed" =~ ^[1-9][0-9]*$ && "$healthy" -ge "$healthy_needed" && "$cur" -lt "$max" ]]; then
    next=$((cur + 1))
  fi

  printf '%s\n' "$next"
}

# null_dev_update_baseline BASELINE_MS DURATION_MS [ALPHA]
# EWMA of successful chunk durations (ms). Prints new baseline.
null_dev_update_baseline() {
  local baseline="${1:-0}"
  local duration="${2:-0}"
  local alpha="${3:-0.2}"
  if ! [[ "$duration" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "$baseline"
    return 0
  fi
  if ! [[ "$baseline" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "$duration"
    return 0
  fi
  awk -v b="$baseline" -v d="$duration" -v a="$alpha" 'BEGIN { printf "%d", (a * d) + ((1 - a) * b) }'
}
