#!/bin/bash
# Regression: multi-jid trailing node-wide '-' must not overwrite DEBUG shm.
# See job_shared_emit.c insert_serialized(..., mirror_shm).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/src/job_shared_emit.c"

test -f "${SRC}" || {
  echo "missing ${SRC}" >&2
  exit 1
}

# Trailing N>=2 node-wide insert must pass mirror_shm=0.
if ! grep -nE 'insert_serialized\(w, "-", NULL, NULL, 0,' "${SRC}" | grep -q .; then
  echo "expected insert_serialized(..., 0, ...) for trailing multi-jid node-wide '-'" >&2
  exit 1
fi

# Idle path and jid inserts must still mirror (mirror_shm=1).
idle_mirrors="$(grep -cE 'insert_serialized\(w, "-", NULL, NULL, 1,' "${SRC}" || true)"
if test "${idle_mirrors}" -lt 1; then
  echo "expected idle '-' insert_serialized(..., 1, ...) so empty discovery still updates shm" >&2
  exit 1
fi

jid_mirrors="$(grep -cE 'insert_serialized\(w, disc\.entries\[.*, 1,' "${SRC}" || true)"
if test "${jid_mirrors}" -lt 1; then
  echo "expected at least one jid insert_serialized(..., 1, ...)" >&2
  exit 1
fi

# Burst API must be wired so operators can see the full tick in *.burst.
grep -q 'stats_buffer_debug_shm_burst_begin' "${SRC}" \
  || {
    echo "job_shared_emit must call stats_buffer_debug_shm_burst_begin" >&2
    exit 1
  }
grep -q 'stats_buffer_debug_shm_burst_append' "${SRC}" \
  || {
    echo "job_shared_emit must call stats_buffer_debug_shm_burst_append" >&2
    exit 1
  }
grep -q 'stats_buffer_debug_shm_burst_commit' "${SRC}" \
  || {
    echo "job_shared_emit must call stats_buffer_debug_shm_burst_commit" >&2
    exit 1
  }

# Idle '-' (no jids) is the whole-node remainder; c122-018 2026-11-05 dropped all GPU rows.
grep -q 'job_emit_remainder_ctx(&available, NULL, available.inventory_known' "${SRC}" \
  || {
    echo "idle '-' must use job_emit_remainder_ctx(&available, NULL, ...) to keep GPU rows" >&2
    exit 1
  }

# |J|>=2 host '-' must emit unscheduled GPUs (remainder) and affinity-split CPUs.
grep -q 'job_gpu_set_diff' "${SRC}" \
  || {
    echo "job_shared_emit must compute GPU remainder via job_gpu_set_diff" >&2
    exit 1
  }
grep -q 'job_sched_affinity_remainder_cpulist' "${SRC}" \
  || {
    echo "job_shared_emit must compute CPU remainder via job_sched_affinity_remainder_cpulist" >&2
    exit 1
  }
# Row filter (CPU affinity split) lives in the linkable job_emit_filter.c unit.
grep -q 'is_cpu_type' "${ROOT}/src/job_emit_filter.c" \
  || {
    echo "job_emit_filter must filter host_cpu/host_cpu_hw by affinity" >&2
    exit 1
  }

# Idle allocations (title-named jids) never emit %affinity: emit must route through the
# filter helpers that encode that rule (unit-tested in test_job_discovery_marks).
for sym in job_emit_sole_ctx job_emit_multi_ctx job_emit_entry_emits_affinity; do
  grep -q "${sym}" "${SRC}" \
    || {
      echo "job_shared_emit must call ${sym}" >&2
      exit 1
    }
done

# M1: CPU coverage for %affinity / the sole fast path reads the extern stepd (job cpuset)
# through job_emit_entry_cpulist, never a raw task-pid union in the emit path.
grep -q 'job_emit_entry_cpulist' "${SRC}" \
  || {
    echo "job_shared_emit must compute CPU coverage via job_emit_entry_cpulist" >&2
    exit 1
  }
if grep -q 'job_sched_affinity_union_cpulist' "${SRC}"; then
  echo "job_shared_emit must not call job_sched_affinity_union_cpulist directly (M1)" >&2
  exit 1
fi
# M3: a sole marked jid uses the split path, so exactly one jid insert carries no marks
# (the sole fast path) and the '-' remainder ctx comes from the linkable helper.
sole_unmarked="$(grep -cE 'insert_serialized\(w, disc\.entries\[0\]\.jid, NULL, NULL, 1,' "${SRC}" || true)"
if test "${sole_unmarked}" -ne 1; then
  echo "expected exactly one unmarked sole insert (got ${sole_unmarked})" >&2
  exit 1
fi
if grep -qE 'insert_serialized\(w, disc\.entries\[0\]\.jid, jobs_mark' "${SRC}"; then
  echo "sole marked jid must use the per-jid + '-' split path, not a sole-only insert (M3)" >&2
  exit 1
fi
grep -q 'job_emit_remainder_ctx' "${SRC}" \
  || {
    echo "job_shared_emit must build the '-' remainder via job_emit_remainder_ctx" >&2
    exit 1
  }

echo "test_job_shared_emit_debug_shm_mirror passed"
