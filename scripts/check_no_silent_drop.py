#!/usr/bin/env python3
"""Fail if the queue orchestrator still pop-then-lease (silent drop).

Attributes:
  FORBIDDEN: Function names that must not appear in the orchestrator.
  ORCH: Path to ``sync_timedb_queue_orchestrator.py``.
  ROOT: Git checkout root containing ``scripts/``.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "hpcperfstats" / "dbload" / "lib" / "sync_timedb_queue_orchestrator.py"

FORBIDDEN = ("pop_ingest_job_ranged", "pop_list_job")


def main() -> int:
  """
  Exit 0 when fill/reconstruct paths no longer pop-then-lease.

  Returns:
    int: ``0`` when the orchestrator is clean, ``1`` otherwise.

  Examples:
    >>> FORBIDDEN[0]
    'pop_ingest_job_ranged'
  """
  src = ORCH.read_text(encoding="utf-8")
  found = [name for name in FORBIDDEN if name in src]
  if found:
    print("silent drop APIs still referenced: %s" % ", ".join(found))
    return 1
  print("NO_SILENT_DROP_OK")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
