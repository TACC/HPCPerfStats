#!/usr/bin/env bash
# Invoked inside the pipeline container by tests/run_sync_timedb_benchmark_workflow.sh.
# Uses free-threaded /opt/python3.14t for sync_timedb benchmark ABI parity.
set -euo pipefail

cd /home/hpcperfstats

PYT=/opt/python3.14t/bin/python
if [[ ! -x "$PYT" ]]; then
  echo "missing free-threaded interpreter: $PYT" >&2
  exit 1
fi

echo "Installing editable test deps into 3.14t (or PYTHONPATH fallback)..."
if ! "$PYT" -m pip install -q -e ".[test]"; then
  echo "3.14t pip install -e failed; using PYTHONPATH + pytest fallback."
  export PYTHONPATH=/home/hpcperfstats${PYTHONPATH:+:$PYTHONPATH}
  "$PYT" -m pip install -q \
    "Django>=6.0.6,<7.0" \
    "pytest>=9.0" \
    "pytest-django>=4.12.0" \
    "pytest-cov>=7.1.0"
fi

export HPCPERFSTATS_SYNC_TIMEDB_BENCH=1
export HPCPERFSTATS_COMPOSE_NETWORK=1

PYTEST_EXTRA=()
if [[ -f /tmp/hpcperfstats_pytest_extra_args ]]; then
  mapfile -t PYTEST_EXTRA < /tmp/hpcperfstats_pytest_extra_args
fi

PYTEST_TARGET=(tests/sync_timedb_benchmark)
if [[ "${HPCPERFSTATS_SYNC_TIMEDB_E2:-}" == "1" ]]; then
  PYTEST_TARGET=(
    tests/sync_timedb_benchmark/test_e2_closed_book_mid_size.py
  )
elif [[ "${HPCPERFSTATS_SYNC_TIMEDB_KNOBS:-}" == "1" ]]; then
  PYTEST_TARGET=(
    tests/sync_timedb_benchmark/test_supporting_knobs.py
  )
elif [[ "${HPCPERFSTATS_SYNC_TIMEDB_E6:-}" == "1" ]]; then
  PYTEST_TARGET=(
    tests/sync_timedb_benchmark/test_e6_parse_feed_ab.py
  )
elif [[ "${HPCPERFSTATS_SYNC_TIMEDB_SCREENING:-}" == "1" \
     || "${HPCPERFSTATS_SYNC_TIMEDB_KNEE:-}" == "1" ]]; then
  PYTEST_TARGET=(
    tests/sync_timedb_benchmark/test_ingest_width_screening.py
  )
fi

echo "Running ${PYTEST_TARGET[*]} under 3.14t..."
"$PYT" -c 'import sys; print(sys.version)'
# -s: show replicate progress (print flush) during long knee/screening matrices.
exec env PYTHONUNBUFFERED=1 "$PYT" -m pytest "${PYTEST_TARGET[@]}" -s -q --tb=short "${PYTEST_EXTRA[@]}"
