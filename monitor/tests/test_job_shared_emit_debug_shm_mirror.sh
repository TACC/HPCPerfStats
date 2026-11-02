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

echo "test_job_shared_emit_debug_shm_mirror passed"
