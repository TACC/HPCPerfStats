"""Fixed defaults for former B INI keys removed from the registry.

These are not operator-tunable. Leftover worker helpers that still read the
old values import constants from here until those helpers are deleted.

Attributes:
  SYNC_ARCHIVE_MAINT_HINTS: Former archive maint hints enable flag (default off).
  SYNC_DAY_CLOSE_CANDIDATE_REPORT: Former day-close candidate report flag.
  SYNC_INGEST_GIANT_POOL_SUPPLEMENT_ENABLED: Former giant-pool supplement enable.
  SYNC_INGEST_GIANT_POOL_SUPPLEMENT_LARGE_MAX_BYTES: Hard byte cap for large files.
  SYNC_INGEST_GIANT_POOL_SUPPLEMENT_MAX_BYTES: Soft byte cap for supplement paths.
  SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER: Queue ceiling multiplier.
  SYNC_INGEST_GIANT_POOL_SUPPLEMENT_TRIGGER_BUDGET_S: Stall budget before supplement.
  SYNC_INGEST_IDLE_SLOT_SUPPLEMENT_ENABLED: Former idle-slot supplement enable.
  SYNC_INGEST_RESCAN_FULL_EVERY: Former full-rescan cadence (0 = off).
  SYNC_PROCESS_TREE_RSS_CHECK_EVERY_N_CHUNKS: Process-tree RSS check cadence.
  SYNC_STARTUP_SNAPSHOT_WAIT_SECONDS: Startup archive scan wait seconds.
  SYNC_SUPERVISOR_RSS_CHECK_EVERY_N_CHUNKS: Supervisor RSS check cadence.
"""
from __future__ import annotations

SYNC_INGEST_GIANT_POOL_SUPPLEMENT_ENABLED: bool = False
SYNC_INGEST_IDLE_SLOT_SUPPLEMENT_ENABLED: bool = False
SYNC_INGEST_GIANT_POOL_SUPPLEMENT_MAX_BYTES: int = 1024 * 1024 * 1024
SYNC_INGEST_GIANT_POOL_SUPPLEMENT_LARGE_MAX_BYTES: int = 8 * 1024 * 1024 * 1024
SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER: int = 2
SYNC_INGEST_GIANT_POOL_SUPPLEMENT_TRIGGER_BUDGET_S: float = 6600.0
SYNC_INGEST_RESCAN_FULL_EVERY: int = 0
SYNC_SUPERVISOR_RSS_CHECK_EVERY_N_CHUNKS: int = 1
SYNC_PROCESS_TREE_RSS_CHECK_EVERY_N_CHUNKS: int = 1
SYNC_DAY_CLOSE_CANDIDATE_REPORT: bool = False
SYNC_STARTUP_SNAPSHOT_WAIT_SECONDS: float = 300.0
SYNC_ARCHIVE_MAINT_HINTS: bool = False


def sync_ingest_giant_pool_supplement_queue_size(
  queue_max: int,
  *,
  multiplier: int | None = None,
) -> int:
  """
  Former supplement queue ceiling: ``queue_max * multiplier`` (minimum 1).

  Args:
    queue_max (int): Base ingest queue max size.
    multiplier (int | None): Optional override; defaults to
      ``SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER``.

  Returns:
    int: Positive queue ceiling.

  Examples:
    >>> sync_ingest_giant_pool_supplement_queue_size(3000, multiplier=2)
    6000
  """
  mult = (
      SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER
      if multiplier is None
      else int(multiplier)
  )
  return max(1, int(queue_max) * int(mult))
