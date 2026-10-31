"""
Opt-in per-job wall-time attribution for the update_metrics compute path.

Stage 0 of the columnar plan needs to know where a job's wall time goes:
``db_fetch`` (every ``host_data`` SQL on the job path), ``frame_build``
(pandas pivots in ``_JobForMetrics``), ``metrics_compute`` (metric math and
pandas bucketing), ``detail_build`` (Job Detail payload assembly),
``plot_series`` (aggregate assembly and merges for plots), ``bokeh`` (figure
construction and ``json_item``) and ``persist`` (metrics and artifact
upserts). Phases nest; each phase is charged only its **exclusive** time
(children are subtracted), so the phase totals of one job add up to the
profiled wall time.

Profiling is off unless ``HPCPERFSTATS_METRICS_PHASE_PROFILE`` is truthy (a
dev diagnostic, not a production knob). When off every helper is a cheap
no-op so the hooks can stay in the hot path.

Attributes:
  PHASES: Canonical phase names in report order.
  REPLACEABLE_PHASES: Phases the columnar/Codon path replaces (Stage 0 gate).
  ENV_FLAG: Environment variable that enables profiling.
  _ACTIVE: Per-thread active job record (``None`` when not profiling).
  _RECORDS: Finished job records awaiting ``drain_records``.
  _RECORDS_LOCK: Guards ``_RECORDS``.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

PHASES: tuple[str, ...] = (
  "db_fetch",
  "frame_build",
  "metrics_compute",
  "detail_build",
  "plot_series",
  "bokeh",
  "persist",
)
REPLACEABLE_PHASES: frozenset[str] = frozenset(
  {"db_fetch", "frame_build", "metrics_compute", "detail_build", "plot_series"}
)
ENV_FLAG = "HPCPERFSTATS_METRICS_PHASE_PROFILE"

_ACTIVE: ContextVar[dict[str, Any] | None] = ContextVar(
  "hps_metrics_phase_profile", default=None
)
_RECORDS: list[dict[str, Any]] = []
_RECORDS_LOCK = threading.Lock()


def enabled() -> bool:
  """
  Return True when phase profiling is switched on by the environment.

  Returns:
    bool: ``HPCPERFSTATS_METRICS_PHASE_PROFILE`` is ``1``/``true``/``yes``/``on``.

  Examples:
    >>> import os
    >>> os.environ[ENV_FLAG] = "1"
    >>> enabled()
    True
    >>> del os.environ[ENV_FLAG]
    >>> enabled()
    False
  """
  value = os.environ.get(ENV_FLAG, "").strip().lower()
  return value in ("1", "true", "yes", "on")


def _new_record(jid: Any, stage: str) -> dict[str, Any]:
  """
  Build an empty job record with every phase at zero.

  Args:
    jid (Any): Job id the record belongs to.
    stage (str): ``metrics`` or ``prewarm`` (which worker task produced it).

  Returns:
    dict[str, Any]: Record with ``jid``, ``stage``, ``phases``, ``stack``,
    ``t0`` and ``wall_s`` keys.

  Examples:
    >>> sorted(_new_record("1", "metrics")["phases"]) == sorted(PHASES)
    True
  """
  return {
    "jid": str(jid),
    "stage": stage,
    "phases": dict.fromkeys(PHASES, 0.0),
    "stack": [],
    "t0": time.monotonic(),
    "wall_s": 0.0,
  }


@contextlib.contextmanager
def job_scope(jid: Any, stage: str) -> Iterator[dict[str, Any] | None]:
  """
  Profile one worker task for ``jid``; publish the record on exit.

  A no-op (yields ``None``) when profiling is disabled. Nested scopes on
  the same thread keep the outer record (the inner one is ignored) so a
  retry wrapper around an already-profiled call does not split the job.

  Args:
    jid (Any): Job id.
    stage (str): ``metrics`` or ``prewarm``.

  Yields:
    dict[str, Any] | None: The live record, or ``None`` when disabled.

  Examples:
    >>> import os
    >>> os.environ[ENV_FLAG] = "1"
    >>> with job_scope("7", "metrics") as rec:
    ...   with phase("db_fetch"):
    ...     pass
    >>> rec["jid"], rec["stage"], rec["phases"]["db_fetch"] >= 0.0
    ('7', 'metrics', True)
    >>> len(drain_records()) == 1
    True
    >>> del os.environ[ENV_FLAG]
  """
  if not enabled() or _ACTIVE.get() is not None:
    yield None
    return
  record = _new_record(jid, stage)
  token = _ACTIVE.set(record)
  try:
    yield record
  finally:
    record["wall_s"] = time.monotonic() - record["t0"]
    del record["stack"]
    del record["t0"]
    _ACTIVE.reset(token)
    with _RECORDS_LOCK:
      _RECORDS.append(record)


@contextlib.contextmanager
def phase(name: str) -> Iterator[None]:
  """
  Charge the enclosed block's exclusive wall time to ``name``.

  Time spent in nested ``phase`` blocks is subtracted from the enclosing
  phase, so each second is attributed exactly once. Unknown names are
  accepted (they appear as extra keys) so a new hook can be added without
  touching ``PHASES``. A no-op when no job record is active.

  Args:
    name (str): Phase name (normally one of ``PHASES``).

  Yields:
    None

  Examples:
    >>> import os
    >>> os.environ[ENV_FLAG] = "1"
    >>> with job_scope("8", "metrics") as rec:
    ...   with phase("metrics_compute"):
    ...     with phase("db_fetch"):
    ...       pass
    >>> rec["phases"]["metrics_compute"] >= 0.0 and rec["phases"][
    ...   "db_fetch"
    ... ] >= 0.0
    True
    >>> _ = drain_records()
    >>> del os.environ[ENV_FLAG]
  """
  record = _ACTIVE.get()
  if record is None:
    yield
    return
  frame = [name, time.monotonic(), 0.0]
  record["stack"].append(frame)
  try:
    yield
  finally:
    elapsed = time.monotonic() - frame[1]
    record["stack"].pop()
    exclusive = max(0.0, elapsed - frame[2])
    record["phases"][name] = record["phases"].get(name, 0.0) + exclusive
    if record["stack"]:
      record["stack"][-1][2] += elapsed


def drain_records() -> list[dict[str, Any]]:
  """
  Return and clear every finished job record.

  Returns:
    list[dict[str, Any]]: Records in completion order.

  Examples:
    >>> drain_records()
    []
  """
  with _RECORDS_LOCK:
    out = list(_RECORDS)
    _RECORDS.clear()
  return out


def merge_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
  """
  Sum the ``metrics`` and ``prewarm`` records of each job into one row.

  Args:
    records (list[dict[str, Any]]): Records from ``drain_records``.

  Returns:
    dict[str, dict[str, Any]]: ``jid`` -> ``{"phases": {...}, "wall_s": …,
    "stages": [...]}`` with phases summed across stages.

  Examples:
    >>> rows = merge_records(
    ...   [
    ...     {
    ...       "jid": "1",
    ...       "stage": "metrics",
    ...       "wall_s": 2.0,
    ...       "phases": {"db_fetch": 1.0},
    ...     },
    ...     {
    ...       "jid": "1",
    ...       "stage": "prewarm",
    ...       "wall_s": 3.0,
    ...       "phases": {"bokeh": 2.0},
    ...     },
    ...   ]
    ... )
    >>> (
    ...   rows["1"]["wall_s"],
    ...   rows["1"]["phases"]["db_fetch"],
    ...   rows["1"]["phases"]["bokeh"],
    ... )
    (5.0, 1.0, 2.0)
  """
  out: dict[str, dict[str, Any]] = {}
  for rec in records:
    row = out.setdefault(
      str(rec["jid"]),
      {"phases": dict.fromkeys(PHASES, 0.0), "wall_s": 0.0, "stages": []},
    )
    row["wall_s"] += float(rec.get("wall_s") or 0.0)
    row["stages"].append(str(rec.get("stage")))
    for name, seconds in (rec.get("phases") or {}).items():
      row["phases"][name] = row["phases"].get(name, 0.0) + float(seconds)
  return out


def replaceable_fraction(row: dict[str, Any]) -> float:
  """
  Share of a merged row's wall time spent in ``REPLACEABLE_PHASES``.

  Args:
    row (dict[str, Any]): One ``merge_records`` value.

  Returns:
    float: ``sum(replaceable phases) / wall_s`` (0.0 for zero wall).

  Examples:
    >>> replaceable_fraction(
    ...   {
    ...     "wall_s": 10.0,
    ...     "phases": {"db_fetch": 3.0, "bokeh": 5.0, "persist": 2.0},
    ...   }
    ... )
    0.3
  """
  wall = float(row.get("wall_s") or 0.0)
  if wall <= 0.0:
    return 0.0
  phases = row.get("phases") or {}
  total = sum(float(phases.get(name, 0.0)) for name in REPLACEABLE_PHASES)
  return total / wall
