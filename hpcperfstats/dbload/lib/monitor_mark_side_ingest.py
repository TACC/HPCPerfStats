"""Parse monitor ``%jobs`` / ``%affinity`` marks and write affinity rows.

Shared-node samples stamp non-GPU ``host_data.dev`` as ``j:<jid>`` in the
stats parser. That key is the job membership on ``host_data``. This module
parses the marks and upserts ``job_affinity``. Payloads without the marks
are unchanged.

Attributes:
  log: Logger for skipped side-table writes.
  _GPU_TYPES: Stats types that keep a hardware ``dev`` token (the canonical
    ``GPU_STATS_TYPES``; ``host_power_socket`` is node-wide and never on a
    jid payload, D38).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from hpcperfstats.dbload.lib.monitor_naming.canonical import GPU_STATS_TYPES

log = logging.getLogger(__name__)

_GPU_TYPES = GPU_STATS_TYPES


def parse_jobs_jids(line: str) -> list[str]:
  """
  Return jid tokens from a ``%jobs jids=`` line.

  Args:
    line (str): One stats line, optionally with a leading percent.

  Returns:
    list[str]: Jid strings. Empty when the line is not a jobs mark.

  Examples:
    >>> parse_jobs_jids("%jobs jids=12,34")
    ['12', '34']
    >>> parse_jobs_jids("%begin 12")
    []
  """
  text = line.strip()
  if not text.startswith("%jobs"):
    return []
  parts = text.split(None, 1)
  if len(parts) < 2 or not parts[1].startswith("jids="):
    return []
  raw = parts[1][len("jids=") :]
  out: list[str] = []
  for token in raw.split(","):
    jid = token.strip()
    if jid and jid not in ("-", "0") and len(jid) <= 32 and " " not in jid:
      out.append(jid)
  return out


def parse_affinity_mark(line: str) -> dict[str, Any] | None:
  """
  Parse one ``%affinity`` line into field tokens.

  Args:
    line (str): One stats line.

  Returns:
    dict[str, Any] | None: Keys ``jid``, ``cpus``, ``gpus``, and ``cg``,
    or ``None`` when the line is not a usable affinity mark.

  Examples:
    >>> parse_affinity_mark("%affinity jid=9 cpus=0-1 gpus=- cg=sched")["jid"]
    '9'
    >>> parse_affinity_mark("%jobs jids=9") is None
    True
  """
  text = line.strip()
  if not text.startswith("%affinity"):
    return None
  fields: dict[str, str] = {}
  for token in text.split()[1:]:
    if "=" not in token:
      continue
    key, value = token.split("=", 1)
    fields[key] = value
  jid = fields.get("jid", "").strip()
  if not jid or jid in ("-", "0") or len(jid) > 32:
    return None
  return {
    "kind": "affinity",
    "jid": jid,
    "cpus": fields.get("cpus", ""),
    "gpus": fields.get("gpus", "-"),
    "cg": fields.get("cg", "sched"),
  }


def cpu_list_from_token(cpus: str) -> list[str]:
  """
  Split a Linux CPU list token into range pieces.

  Args:
    cpus (str): Value of ``cpus=`` (for example ``0-3,8``).

  Returns:
    list[str]: Pieces. Empty input is an empty list.

  Examples:
    >>> cpu_list_from_token("0-3,8")
    ['0-3', '8']
    >>> cpu_list_from_token("")
    []
  """
  if not cpus or cpus == "-":
    return []
  return [part for part in cpus.split(",") if part]


def gpu_list_from_token(gpus: str) -> list[str]:
  """
  Split a GPU index list. A dash means no devices.

  Args:
    gpus (str): Value of ``gpus=``.

  Returns:
    list[str]: Index strings.

  Examples:
    >>> gpu_list_from_token("0,1")
    ['0', '1']
    >>> gpu_list_from_token("-")
    []
  """
  if not gpus or gpus == "-":
    return []
  return [part for part in gpus.split(",") if part and part != "-"]


def detect_shared_node_contract(lines: Any) -> bool:
  """
  True when any line is a ``%jobs`` mark with at least one jid.

  Args:
    lines (Any): Iterable of stats lines, or one payload string.

  Returns:
    bool: Whether collapsed non-GPU rows should use ``dev=j:<jid>``.

  Examples:
    >>> detect_shared_node_contract(["%jobs jids=9", "1 9 h"])
    True
    >>> detect_shared_node_contract("1 9 h\\ncpu 0 1\\n")
    False
  """
  seq = lines.splitlines() if isinstance(lines, str) else lines or []
  return any(parse_jobs_jids(str(line)) for line in seq)


def shared_dev_for_row(
  typ: str,
  dev: str,
  jid: str | None,
  shared: bool,
) -> str:
  """
  Return the device token to store before collapse.

  Args:
    typ (str): Stats type name.
    dev (str): Monitor device token.
    jid (str | None): Sample-header job id, or ``None`` when idle.
    shared (bool): True when this sample carried ``%jobs``.

  Returns:
    str: ``j:<jid>`` for shared non-GPU rows; otherwise ``dev``.

  Examples:
    >>> shared_dev_for_row("cpu", "0", "42", True)
    'j:42'
    >>> shared_dev_for_row("nvidia_gpu", "0", "42", True)
    '0'
    >>> shared_dev_for_row("cpu", "0", "42", False)
    '0'
  """
  if not shared or typ in _GPU_TYPES or not jid or jid in ("-", "0"):
    return dev
  return f"j:{jid}"[:64]


def job_dev_tokens(jids: list[str]) -> list[str]:
  """
  Return ``host_data.dev`` keys for shared-node job ids.

  Args:
    jids (list[str]): Job ids already truncated to the ``jid`` column width.

  Returns:
    list[str]: ``j:<jid>`` tokens, same stamp as shared non-GPU rows.

  Examples:
    >>> job_dev_tokens(["42", "7"])
    ['j:42', 'j:7']
  """
  return [f"j:{jid}"[:64] for jid in jids]


def collect_marks(lines: Any) -> list[dict[str, Any]]:
  """
  Walk stats lines and attach each mark to the following sample header.

  Args:
    lines (Any): Iterable of lines or one payload string.

  Returns:
    list[dict[str, Any]]: Mark dicts. ``time`` and ``host`` are filled
    when a digit header follows the mark.

  Examples:
    >>> rows = collect_marks(
    ...   ["%jobs jids=7", "1710000001.0 7 hosta", "cpu 0 1"]
    ... )
    >>> rows[0]["jids"]
    ['7']
    >>> rows[0]["host"]
    'hosta'
  """
  seq = lines.splitlines() if isinstance(lines, str) else list(lines or [])
  marks: list[dict[str, Any]] = []
  pending: list[dict[str, Any]] = []
  for raw in seq:
    line = str(raw).strip()
    if not line:
      continue
    if line.startswith("%jobs"):
      jids = parse_jobs_jids(line)
      if not jids:
        continue
      rec: dict[str, Any] = {
        "kind": "jobs",
        "jids": jids,
        "time": None,
        "host": None,
      }
      marks.append(rec)
      pending.append(rec)
      continue
    if line.startswith("%affinity"):
      rec_a = parse_affinity_mark(line)
      if rec_a is None:
        continue
      rec_a["time"] = None
      rec_a["host"] = None
      marks.append(rec_a)
      pending.append(rec_a)
      continue
    if line[0].isdigit():
      parts = line.split()
      if len(parts) < 3:
        continue
      try:
        when = float(parts[0])
      except ValueError:
        continue
      host = parts[2]
      for rec_p in pending:
        if rec_p.get("time") is None:
          rec_p["time"] = when
          rec_p["host"] = host
      pending = []
  return marks


def _as_utc(when: Any) -> datetime | None:
  """
  Convert a unix-second sample time to an aware datetime.

  Args:
    when (Any): Float seconds, or a datetime.

  Returns:
    datetime | None: UTC datetime, or ``None`` when ``when`` is missing.

  Examples:
    >>> _as_utc(1710000001.0).year
    2024
    >>> _as_utc(None) is None
    True
  """
  if when is None:
    return None
  if isinstance(when, datetime):
    if when.tzinfo is None:
      return when.replace(tzinfo=UTC)
    return when
  return datetime.fromtimestamp(float(when), tz=UTC)


def _persist_sentinel_runs(marks: list[dict[str, Any]]) -> None:
  """
  Merge ``kind=sentinel`` run marks into ``host_sentinel_summary``.

  A run continues the stored run of the same series, code and device counts
  whose ``end_time`` reaches the run's previous sample time (``start_time``
  when the series had no earlier sample), so a run split across flushes or
  messages becomes one row. ``start_time``/``end_time`` only widen
  (``min``/``max``), and a run the stored row already covers writes nothing,
  so replaying a file or a re-delivered message changes nothing. All runs of
  one call merge in one transaction: one locking read of the stored rows they
  can touch, then one insert and one update. Archive and live ingest may race
  on the same run; the unique constraint fails the loser's batch, which
  retries once against the winner's rows.

  Args:
    marks (list[dict[str, Any]]): Marks; non-sentinel kinds are ignored.

  Returns:
    None

  Examples:
    >>> _persist_sentinel_runs([]) is None
    True
  """
  runs = [m for m in marks if m.get("kind") == "sentinel"]
  if not runs:
    return
  from django.db import IntegrityError, transaction
  from django.db.models import Q

  from hpcperfstats.site.lib.machine.models import host_sentinel_summary

  key_fields = (
    "host",
    "type",
    "dev",
    "event",
    "code",
    "device_count",
    "total_devices",
  )
  parsed = []
  for rec in runs:
    try:
      typ, dev, event, code, end, n, total, prev = str(rec["jids"]).split("\t")
      start_dt = _as_utc(rec.get("time"))
      end_dt = _as_utc(float(end))
      prev_dt = _as_utc(float(prev)) if prev else start_dt
      key = (str(rec["host"])[:64], typ[:32], dev[:64], event[:64])
      key += (int(code), int(n), int(total))
    except KeyError, TypeError, ValueError:
      continue
    if start_dt is not None and end_dt is not None and rec.get("host"):
      parsed.append((start_dt, end_dt, prev_dt, key))
  parsed.sort(key=lambda p: p[0])
  malformed = len(runs) - len(parsed)
  written = 0
  for _attempt in range(2 if parsed else 0):
    try:
      with transaction.atomic():
        span = Q()
        for key in {p[3] for p in parsed}:
          span |= Q(**dict(zip(key_fields, key, strict=True)))
        stored: dict[tuple[Any, ...], list[Any]] = {}
        for row in (
          host_sentinel_summary.objects.select_for_update()
          .filter(span, end_time__gte=min(p[2] for p in parsed))
          .filter(start_time__lte=max(p[1] for p in parsed))
          .order_by("start_time")
        ):
          row_key = tuple(getattr(row, f) for f in key_fields)
          stored.setdefault(row_key, []).append(row)
        new, dirty = [], {}
        for start_dt, end_dt, prev_dt, key in parsed:
          rows = stored.setdefault(key, [])
          run = next(
            (
              r
              for r in rows
              if r.end_time >= prev_dt and r.start_time <= end_dt
            ),
            None,
          )
          if run is None:
            run = host_sentinel_summary(
              **dict(zip(key_fields, key, strict=True)),
              start_time=start_dt,
              end_time=end_dt,
            )
            new.append(run)
            rows.append(run)
          elif run.start_time > start_dt or run.end_time < end_dt:
            run.start_time = min(run.start_time, start_dt)
            run.end_time = max(run.end_time, end_dt)
            if run.pk is not None:
              dirty[run.pk] = run
          rows.sort(key=lambda r: r.start_time)
        host_sentinel_summary.objects.bulk_create(new)
        host_sentinel_summary.objects.bulk_update(
          list(dirty.values()), ["start_time", "end_time"]
        )
      written = len(parsed)
      break
    except IntegrityError:
      continue
  if written < len(runs):
    log.error(
      "host_sentinel_summary merge incomplete: written=%d malformed=%d "
      "lost=%d of %d runs",
      written,
      malformed,
      len(runs) - written - malformed,
      len(runs),
    )


def persist_monitor_marks(marks: list[dict[str, Any]]) -> None:
  """
  Upsert ``job_affinity`` from ``%affinity`` marks and merge value-sentinel
  runs (``kind=sentinel``) into ``host_sentinel_summary``.

  ``%jobs`` membership is the ``j:<jid>`` stamp on ``host_data``, so this
  function does not write a second table. Missing Django apps or a missing
  ``job_affinity`` table is a no-op so archive parse tests keep running
  before migrate. Write errors are logged and swallowed so a side-table
  failure cannot drop the host_data timestream.

  Args:
    marks (list[dict[str, Any]]): Records from ``collect_marks``.

  Returns:
    None

  Examples:
    >>> persist_monitor_marks([]) is None
    True
  """
  if not marks:
    return
  try:
    from django.apps import apps

    if not apps.ready:
      return
    from hpcperfstats.site.lib.machine.models import job_affinity
  except Exception:
    return
  affinity: dict[tuple[str, str], dict[str, Any]] = {}
  try:
    for rec in marks:
      if rec.get("kind") != "affinity":
        continue
      when = _as_utc(rec.get("time"))
      host = rec.get("host")
      if when is None or not host:
        continue
      host_s = str(host)[:64]
      jid_s = str(rec["jid"])[:32]
      affinity[(jid_s, host_s)] = {
        "cpu_list": cpu_list_from_token(str(rec.get("cpus") or "")),
        "gpu_list": gpu_list_from_token(str(rec.get("gpus") or "-")),
        "cgroup_controls": {
          "source": "sched_getaffinity",
          "cg": rec.get("cg") or "sched",
        },
        "reported_at": when,
        "source": "monitor",
      }
    for (jid_s, host_s), defaults in affinity.items():
      job_affinity.objects.update_or_create(
        jid=jid_s,
        host=host_s,
        defaults=defaults,
      )
  except Exception:
    log.warning("monitor mark side ingest skipped", exc_info=True)
  # Own boundary: a failed run merge must not hide behind (or abort) the
  # affinity upsert, and must be loud: host_data keeps the sentinel rows, but
  # reasons, partial-device scaling and Admin Monitor read this table.
  try:
    _persist_sentinel_runs(marks)
  except Exception:
    runs_n = sum(1 for m in marks if m.get("kind") == "sentinel")
    log.error(
      "host_sentinel_summary write failed for %d sentinel runs",
      runs_n,
      exc_info=True,
    )


def flush_parser_side_marks(parser: Any) -> None:
  """
  Write marks the parser has already bound to a sample header.

  Args:
    parser (Any): ``IncrementalStatsParser`` with ``take_ready_marks``.

  Returns:
    None

  Examples:
    >>> flush_parser_side_marks(object()) is None
    True
  """
  take = getattr(parser, "take_ready_marks", None)
  if not callable(take):
    return
  persist_monitor_marks(take())


def max_host_time_by_jid(jids: Any) -> dict[str, Any]:
  """
  Latest ``host_data.time`` for each job.

  Exclusive rows match ``host_data.jid``. Shared-node rows match
  ``host_data.dev`` ``j:<jid>``. A job present on both keeps the later time.

  Args:
    jids (Any): One jid string, or an iterable of jid strings.

  Returns:
    dict[str, Any]: Jid to max sample time. Empty when ``jids`` is empty.

  Examples:
    >>> max_host_time_by_jid([])
    {}
  """
  jid_list = [jids] if isinstance(jids, str) else [str(jid) for jid in jids]
  if not jid_list:
    return {}
  dev_to_jid = dict(zip(job_dev_tokens(jid_list), jid_list, strict=True))
  from django.db.models import Max

  from hpcperfstats.site.lib.machine.models import host_data

  found: dict[str, Any] = {}
  queries = (
    (
      host_data.objects.filter(jid__in=jid_list)
      .values("jid")
      .annotate(max_time=Max("time")),
      "jid",
      None,
    ),
    (
      host_data.objects.filter(dev__in=list(dev_to_jid))
      .values("dev")
      .annotate(max_time=Max("time")),
      "dev",
      dev_to_jid,
    ),
  )
  for rows, key, mapping in queries:
    for row in rows:
      jid = mapping.get(row[key]) if mapping else row[key]
      when = row["max_time"]
      if not jid or when is None:
        continue
      jid_s = str(jid)
      prev = found.get(jid_s)
      if prev is None or when > prev:
        found[jid_s] = when
  return found
