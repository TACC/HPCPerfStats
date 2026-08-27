"""
Brownfield reconstruct helpers for sync_timedb greenfield job queues.

Library-only (slice 2): classify closed raw paths and day-close targets from
disk + Timescale + durable marks, enqueue only when complete predicates are
false, and encode the laws that empty Redis job queues are not \"caught up\"
and ``.sync_timedb_state.json`` is not reconstruct source of truth. Not wired
into ``sync_timedb.py`` until the orchestrator cutover slice.

Attributes:
  DAY_CLOSE_MIN_AGE_HOURS_DEFAULT: Post day-end age before day_close is due.
  DEFAULT_INGEST_HOT_DAYS: Hot-band window when INI is not consulted yet.
  RECONSTRUCT_CHECKPOINT_BASENAME: Checkpoint sidecar name (not reconstruct
    SoT).
  RECONSTRUCT_SOURCES: Documented reconstruct sources of truth.
"""
from __future__ import annotations

from typing import Any, Callable

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import os

from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq

RECONSTRUCT_CHECKPOINT_BASENAME = ".sync_timedb_state.json"
RECONSTRUCT_SOURCES = frozenset({"disk", "timescale", "marks"})
DAY_CLOSE_MIN_AGE_HOURS_DEFAULT = 32.0
DEFAULT_INGEST_HOT_DAYS = 8


def empty_job_queues_mean_caught_up() -> bool:
  """
  Return whether empty Redis job structures imply the archive is caught up.

  Law (OQ-1 / residual gaps): empty ``job:v1`` queues are never \"caught up\".
  Reconstruct always classifies from disk + Timescale + marks.

  Returns:
    bool: Always ``False``.

  Examples:
    >>> empty_job_queues_mean_caught_up()
    False
  """
  return False


def checkpoint_sidecar_is_reconstruct_source_of_truth() -> bool:
  """
  Return whether ``.sync_timedb_state.json`` is reconstruct source of truth.

  Law: checkpoint may be unread at cutover; marks + disk + Timescale decide
  would-enqueue / enqueue. This helper returns ``False`` so callers cannot
  treat the sidecar as SoT by accident.

  Returns:
    bool: Always ``False``.

  Examples:
    >>> checkpoint_sidecar_is_reconstruct_source_of_truth()
    False
    >>> RECONSTRUCT_CHECKPOINT_BASENAME
    '.sync_timedb_state.json'
  """
  return False


def reconstruct_sources_of_truth() -> frozenset[str]:
  """
  Return the frozen set of reconstruct sources of truth labels.

  Returns:
    frozenset[str]: ``{\"disk\", \"timescale\", \"marks\"}``.

  Examples:
    >>> "marks" in reconstruct_sources_of_truth()
    True
    >>> RECONSTRUCT_CHECKPOINT_BASENAME in reconstruct_sources_of_truth()
    False
  """
  return RECONSTRUCT_SOURCES


def _default_listend_enabled() -> bool:
  """
  Read ``listend_db_ingest_enabled`` from conf; False on any config error.

  Returns:
    bool: Live listend DB ingest switch.

  Examples:
    >>> isinstance(_default_listend_enabled(), bool)
    True
  """
  from hpcperfstats.dbload.lib import conf_parser as cfg

  try:
    return bool(cfg.get_listend_db_ingest_enabled())
  except Exception:
    return False


def ingest_is_complete(
  path: str,
  *,
  archive_data_dir: str | None = None,
  listend_enabled: bool | None = None,
  has_file_complete_fn: Callable[[str], bool] | None = None,
  has_zero_host_fn: Callable[[str], bool] | None = None,
  head_tail_ready_fn: Callable[[str], bool] | None = None,
) -> bool:
  """
  True when the ingest complete predicate holds for a closed raw path.

  Complete = file-complete mark or zero-host mark; when listend is off, also
  accept Timescale head+tail readiness. When listend is on, never treat
  live-only head+tail as complete (marks only).

  Args:
    path (str): Closed raw stats path.
    archive_data_dir (str | None): Archive root for mark sidecars; ``None``
      uses each mark helper's default archive dir.
    listend_enabled (bool | None): Override for live listend; ``None`` reads
      INI via :func:`_default_listend_enabled`.
    has_file_complete_fn (Callable[[str], bool] | None): Injectable
      file-complete mark probe.
    has_zero_host_fn (Callable[[str], bool] | None): Injectable zero-host
      mark probe.
    head_tail_ready_fn (Callable[[str], bool] | None): Injectable head+tail
      Timescale probe used only when listend is off.

  Returns:
    bool: True when reconstruct must **not** ``ZADD`` ingest for ``path``.

  Examples:
    >>> ingest_is_complete(
    ...   "/x",
    ...   listend_enabled=True,
    ...   has_file_complete_fn=lambda p: True,
    ...   has_zero_host_fn=lambda p: False,
    ... )
    True
    >>> ingest_is_complete(
    ...   "/x",
    ...   listend_enabled=True,
    ...   has_file_complete_fn=lambda p: False,
    ...   has_zero_host_fn=lambda p: False,
    ...   head_tail_ready_fn=lambda p: True,
    ... )
    False
  """
  text = str(path or "").strip()
  if not text:
    return False

  if has_file_complete_fn is not None:
    file_ok = bool(has_file_complete_fn(text))
  else:
    from hpcperfstats.dbload.lib.sync_timedb_file_complete_ingest_mark import (
        has_file_complete_ingest_mark,
    )
    file_ok = bool(
        has_file_complete_ingest_mark(
            text,
            archive_data_dir=archive_data_dir,
        )
    )

  if has_zero_host_fn is not None:
    zero_ok = bool(has_zero_host_fn(text))
  else:
    from hpcperfstats.dbload.lib.sync_timedb_zero_host_ingest_mark import (
        has_zero_host_ingest_mark,
    )
    zero_ok = bool(
        has_zero_host_ingest_mark(
            text,
            archive_data_dir=archive_data_dir,
        )
    )

  if file_ok or zero_ok:
    return True

  live = (
      bool(listend_enabled)
      if listend_enabled is not None
      else _default_listend_enabled()
  )
  if live:
    return False

  if head_tail_ready_fn is not None:
    return bool(head_tail_ready_fn(text))

  from hpcperfstats.dbload.lib.sync_timedb_ingest_readiness import (
      stats_file_head_ingested_in_db,
  )
  return bool(stats_file_head_ingested_in_db(text))


def append_is_complete(
  path: str,
  tgz_archive_dir: str,
  *,
  needs_append_fn: Callable[[str, str], bool] | None = None,
) -> bool:
  """
  True when open-tar / sealed membership already matches the closed raw.

  Complete = not :func:`raw_stats_path_needs_tar_append` (AR-06 open-tar
  authority when mutable ``.tar`` exists).

  Args:
    path (str): Closed raw stats path.
    tgz_archive_dir (str): Daily archive directory.
    needs_append_fn (Callable[[str, str], bool] | None): Injectable
      ``needs_append`` probe; default uses archive helpers.

  Returns:
    bool: True when reconstruct must **not** ``RPUSH`` append for ``path``.

  Examples:
    >>> append_is_complete(
    ...   "/raw/a",
    ...   "/daily",
    ...   needs_append_fn=lambda p, d: False,
    ... )
    True
    >>> append_is_complete(
    ...   "/raw/a",
    ...   "/daily",
    ...   needs_append_fn=lambda p, d: True,
    ... )
    False
  """
  text = str(path or "").strip()
  archive = str(tgz_archive_dir or "").strip()
  if not text or not archive:
    return False
  if needs_append_fn is not None:
    needs = bool(needs_append_fn(text, archive))
  else:
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        raw_stats_path_needs_tar_append,
    )
    needs = bool(raw_stats_path_needs_tar_append(text, archive))
  return not needs


def discover_raw_needs_tar_append(
  stats_path: str,
  tgz_archive_dir: str,
  *,
  first_ts: Any | None = None,
  enqueue_populate_on_cold: bool = True,
) -> bool:
  """
  Discover/reconstruct skip-complete probe that never waits on sealed populate.

  Open mutable ``.tar`` and warm Redis ``HGET`` may still skip enqueue. Cold
  Redis (or Redis disabled with sealed-only day) returns ``True`` so discover
  enqueues append (at-least-once). Workers keep
  :func:`raw_stats_path_needs_tar_append` / ``populate_and_wait``.

  Args:
    stats_path (str): Closed raw stats path.
    tgz_archive_dir (str): Daily archive directory.
    first_ts (Any | None): Optional first timestamp hint for day derive.
    enqueue_populate_on_cold (bool): When True and Redis L2 is cold with a
      populate source, fire-and-forget enqueue populate-pool work.

  Returns:
    bool: True when discover should enqueue an append job for ``stats_path``.

  Examples:
    >>> discover_raw_needs_tar_append("/nope", "/daily")
    False
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _daily_archive_members_cache_key,
      _derive_stats_path_date,
      _lookup_daily_archive_members_cache,
      daily_archive_populate_source_exists,
      daily_compressed_path_for_date,
      daily_tar_path_from_compressed,
      get_mutable_tar_authority_member_map,
      get_tar_member_name,
      normalize_daily_compressed_path,
      stats_file_is_active_segment,
  )

  text = str(stats_path or "").strip()
  archive = str(tgz_archive_dir or "").strip()
  if not text or not archive:
    return False
  if not os.path.isfile(text):
    return False
  if stats_file_is_active_segment(text):
    return False
  file_date = _derive_stats_path_date(text, first_ts)
  if file_date is None:
    return False
  try:
    expected_size = os.path.getsize(text)
  except OSError:
    return True
  member_name = get_tar_member_name(text)
  compressed_path = daily_compressed_path_for_date(archive, file_date)
  canonical = normalize_daily_compressed_path(compressed_path)
  tar_path = daily_tar_path_from_compressed(canonical)
  if os.path.isfile(tar_path):
    open_members = get_mutable_tar_authority_member_map(tar_path)
    if open_members.get(member_name) == expected_size:
      return False
    return True
  if not daily_archive_populate_source_exists(canonical):
    return True
  members = _lookup_daily_archive_members_cache(compressed_path)
  if members is not None:
    return members.get(member_name) != expected_size
  try:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        archive_members_redis_enabled,
        build_archive_members_redis_keys,
        enqueue_archive_members_populate,
        get_archive_members_redis_client,
        redis_member_match_when_warm,
    )
  except Exception:
    return True
  if not archive_members_redis_enabled():
    return True
  warm: bool | None = None
  try:
    cache_key = _daily_archive_members_cache_key(canonical)
    keys = build_archive_members_redis_keys(cache_key)
    client = get_archive_members_redis_client(required=True)
    warm = redis_member_match_when_warm(
        keys, member_name, expected_size, client=client,
    )
    if warm is True:
      return False
    if warm is False:
      return True
  except Exception:
    warm = None
  if enqueue_populate_on_cold:
    try:
      enqueue_archive_members_populate(canonical, file_date.isoformat())
    except Exception:
      pass
  return True


def discover_append_is_complete(
  path: str,
  tgz_archive_dir: str,
  **_kwargs: Any,
) -> bool:
  """
  Discover-scoped ``append_is_complete`` that never blocks on sealed populate.

  Args:
    path (str): Closed raw stats path.
    tgz_archive_dir (str): Daily archive directory.
    **_kwargs (Any): Extra kwargs from classify injectables (ignored).

  Returns:
    bool: True when discover must **not** enqueue append for ``path``.

  Examples:
    >>> discover_append_is_complete("/nope", "/daily")
    True
  """
  return not discover_raw_needs_tar_append(path, tgz_archive_dir)


def select_ingest_band(
  day: date,
  *,
  today: date,
  hot_days: int = DEFAULT_INGEST_HOT_DAYS,
) -> str:
  """
  Choose ``hot`` or ``catchup`` for an ingest identity's calendar day.

  Args:
    day (date): Calendar day of the raw file.
    today (date): Local \"today\" for the hot window.
    hot_days (int): Inclusive hot window length (default 8).

  Returns:
    str: ``\"hot\"`` when ``0 <= (today - day).days < hot_days``, else
    ``\"catchup\"``.

  Examples:
    >>> select_ingest_band(date(2026, 8, 20), today=date(2026, 8, 24), hot_days=8)
    'hot'
    >>> select_ingest_band(date(2026, 6, 1), today=date(2026, 8, 24), hot_days=8)
    'catchup'
  """
  age = (today - day).days
  if age < 0:
    return "hot"
  if age < max(1, int(hot_days)):
    return "hot"
  return "catchup"


def day_close_min_age_elapsed(
  day: date,
  *,
  now: datetime | None = None,
  min_age_hours: float = DAY_CLOSE_MIN_AGE_HOURS_DEFAULT,
) -> bool:
  """
  True when local wall clock is at least ``min_age_hours`` past day end.

  Day end is treated as the start of the next calendar day in the same tz as
  ``now`` (naive ``now`` is assumed local wall time).

  Args:
    day (date): Calendar day being closed.
    now (datetime | None): Wall clock; default ``datetime.now()``.
    min_age_hours (float): Hours after day end (default 32).

  Returns:
    bool: True when day_close age gate is satisfied.

  Examples:
    >>> day_close_min_age_elapsed(
    ...   date(2026, 8, 1),
    ...   now=datetime(2026, 8, 3, 12, 0, 0),
    ...   min_age_hours=32.0,
    ... )
    True
    >>> day_close_min_age_elapsed(
    ...   date(2026, 8, 1),
    ...   now=datetime(2026, 8, 2, 0, 0, 0),
    ...   min_age_hours=32.0,
    ... )
    False
  """
  clock = now if now is not None else datetime.now()
  day_end = datetime(day.year, day.month, day.day) + timedelta(days=1)
  if clock.tzinfo is not None and day_end.tzinfo is None:
    day_end = day_end.replace(tzinfo=clock.tzinfo)
  elif clock.tzinfo is None and day_end.tzinfo is not None:
    day_end = day_end.replace(tzinfo=None)
  return clock >= (day_end + timedelta(hours=float(min_age_hours)))


def day_close_is_complete(
  tar_path: str,
  *,
  calendar_day: date | None = None,
  filesystem_complete: bool | None = None,
  filesystem_complete_fn: Callable[[str], bool] | None = None,
  min_age_elapsed: bool | None = None,
  now: datetime | None = None,
  min_age_hours: float = DAY_CLOSE_MIN_AGE_HOURS_DEFAULT,
  phase_name: str | None = None,
) -> bool:
  """
  True when day_close complete predicates hold (filesystem + 32h min-age).

  Manifest / removal ``phase_name`` (including ghost ``phase=done``) is
  **ignored** — it is not reconstruct source of truth.

  Args:
    tar_path (str): Daily ``YYYY-MM-DD.tar`` path (may be absent on disk).
    calendar_day (date | None): Day for min-age; when ``None``, parsed from
      ``tar_path`` basename when possible.
    filesystem_complete (bool | None): Injected FS-complete result.
    filesystem_complete_fn (Callable[[str], bool] | None): Probe used when
      ``filesystem_complete`` is ``None``.
    min_age_elapsed (bool | None): Injected min-age result.
    now (datetime | None): Clock for min-age when not injected.
    min_age_hours (float): Min-age hours (default 32).
    phase_name (str | None): Ignored day-raw-removal / hint phase.

  Returns:
    bool: True when reconstruct must **not** enqueue ``day_close``.

  Examples:
    >>> day_close_is_complete(
    ...   "/d/2026-08-01.tar",
    ...   calendar_day=date(2026, 8, 1),
    ...   filesystem_complete=True,
    ...   min_age_elapsed=True,
    ...   phase_name="done",
    ... )
    True
    >>> day_close_is_complete(
    ...   "/d/2026-08-01.tar",
    ...   calendar_day=date(2026, 8, 1),
    ...   filesystem_complete=False,
    ...   min_age_elapsed=True,
    ...   phase_name="done",
    ... )
    False
  """
  del phase_name  # Ghost phase=done must not authorize complete.
  tar_norm = os.path.normpath(str(tar_path or ""))
  if not tar_norm:
    return False

  if filesystem_complete is None:
    if filesystem_complete_fn is not None:
      filesystem_complete = bool(filesystem_complete_fn(tar_norm))
    else:
      from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
          day_close_filesystem_complete,
      )
      filesystem_complete = bool(day_close_filesystem_complete(tar_norm))
  if not bool(filesystem_complete):
    return False

  if min_age_elapsed is None:
    day = calendar_day
    if day is None:
      base = os.path.basename(tar_norm)
      stem = base[:-4] if base.endswith(".tar") else base
      try:
        day = date.fromisoformat(stem)
      except ValueError:
        return False
    min_age_elapsed = day_close_min_age_elapsed(
        day,
        now=now,
        min_age_hours=min_age_hours,
    )
  return bool(min_age_elapsed)


@dataclass(frozen=True)
class ClosedPathReconstructPlan:
  """
  Would-enqueue plan for one closed raw stats path.

  Attributes:
    path: Normalized closed raw path.
    identity: Ingest ZSET identity (stable normalized path).
    fingerprint: Size/mtime encoding stored on the payload HASH.
    needs_ingest: True when ingest complete predicate is false.
    needs_append: True when append complete predicate is false.
    calendar_day: Resolved calendar day, if known.
    tar_path: Daily tar path for the day, if known.
  """

  path: str
  identity: str
  needs_ingest: bool
  needs_append: bool
  calendar_day: date | None
  tar_path: str | None
  fingerprint: str = ""

  def kinds_to_enqueue(self) -> tuple[str, ...]:
    """
    Return job kinds that would be enqueued for this plan.

    Returns:
      tuple[str, ...]: Ordered kinds among ``ingest`` / ``append``.

    Examples:
      >>> ClosedPathReconstructPlan(
      ...   path="/a",
      ...   identity="/a|1|2",
      ...   needs_ingest=True,
      ...   needs_append=True,
      ...   calendar_day=None,
      ...   tar_path=None,
      ... ).kinds_to_enqueue()
      ('ingest', 'append')
    """
    kinds: list[str] = []
    if self.needs_ingest:
      kinds.append(jq.JOB_KIND_INGEST)
    if self.needs_append:
      kinds.append(jq.JOB_KIND_APPEND)
    return tuple(kinds)


def classify_closed_raw_path(
  path: str,
  *,
  tgz_archive_dir: str,
  size: int,
  mtime_ns: int,
  calendar_day: date | None = None,
  tar_path: str | None = None,
  archive_data_dir: str | None = None,
  listend_enabled: bool | None = None,
  ingest_is_complete_fn: Callable[..., bool] | None = None,
  append_is_complete_fn: Callable[..., bool] | None = None,
) -> ClosedPathReconstructPlan:
  """
  Classify one closed raw path into would-enqueue ingest/append needs.

  Does not read ``.sync_timedb_state.json``. Callers inject complete
  predicates for host unit tests; production defaults use marks / open-tar.

  Args:
    path (str): Closed raw stats path.
    tgz_archive_dir (str): Daily archive directory.
    size (int): Byte size fingerprint for ingest identity.
    mtime_ns (int): ``st_mtime_ns`` fingerprint for ingest identity.
    calendar_day (date | None): Optional known calendar day.
    tar_path (str | None): Optional known daily tar path.
    archive_data_dir (str | None): Archive root for mark helpers.
    listend_enabled (bool | None): Live listend override for ingest complete.
    ingest_is_complete_fn (Callable[..., bool] | None): Injectable ingest
      complete predicate.
    append_is_complete_fn (Callable[..., bool] | None): Injectable append
      complete predicate.

  Returns:
    ClosedPathReconstructPlan: Would-enqueue plan (may enqueue nothing).

  Examples:
    >>> p = classify_closed_raw_path(
    ...   "/raw/a",
    ...   tgz_archive_dir="/daily",
    ...   size=1,
    ...   mtime_ns=2,
    ...   calendar_day=date(2026, 8, 1),
    ...   ingest_is_complete_fn=lambda **k: False,
    ...   append_is_complete_fn=lambda **k: False,
    ... )
    >>> p.needs_ingest and p.needs_append
    True
  """
  norm = os.path.normpath(str(path or ""))
  identity = jq.ingest_identity(norm, size, mtime_ns)
  fingerprint = jq.ingest_fingerprint(size, mtime_ns)
  archive = str(tgz_archive_dir or "").strip()

  if ingest_is_complete_fn is not None:
    complete_ingest = bool(
        ingest_is_complete_fn(
            path=norm,
            archive_data_dir=archive_data_dir,
            listend_enabled=listend_enabled,
        )
    )
  else:
    complete_ingest = ingest_is_complete(
        norm,
        archive_data_dir=archive_data_dir,
        listend_enabled=listend_enabled,
    )

  if append_is_complete_fn is not None:
    complete_append = bool(
        append_is_complete_fn(
            path=norm,
            tgz_archive_dir=archive,
        )
    )
  else:
    complete_append = append_is_complete(norm, archive)

  resolved_tar = tar_path
  if resolved_tar is None and calendar_day is not None and archive:
    resolved_tar = os.path.normpath(
        os.path.join(archive, "%s.tar" % calendar_day.isoformat())
    )

  return ClosedPathReconstructPlan(
      path=norm,
      identity=identity,
      needs_ingest=not complete_ingest,
      needs_append=not complete_append,
      calendar_day=calendar_day,
      tar_path=resolved_tar,
      fingerprint=fingerprint,
  )


def enqueue_reconstruct_jobs_for_closed_path(
  client: Any,
  plan: ClosedPathReconstructPlan,
  *,
  today: date,
  hot_days: int = DEFAULT_INGEST_HOT_DAYS,
  archive_data_dir: str | None = None,
) -> dict[str, bool]:
  """
  Enqueue ingest/append jobs only for incomplete predicates on ``plan``.

  Skips ``ZADD`` / ``RPUSH`` when the corresponding complete predicate is
  already true (``needs_*`` false). Empty Redis before this call still does
  not mean caught up.

  Args:
    client (Any): Redis client supporting ``zadd`` / ``rpush``.
    plan (ClosedPathReconstructPlan): Classify result for one closed path.
    today (date): Local today for hot/catchup score encode.
    hot_days (int): Hot-band window length.
    archive_data_dir (str | None): Archive root for queue dead-letter skip.

  Returns:
    dict[str, bool]: ``{\"ingest\": bool, \"append\": bool}`` for what was
    enqueued.

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.z = {}; self.l = {}
    ...   def zadd(self, key, mapping):
    ...     self.z.update(mapping); return 1
    ...   def rpush(self, key, *vals):
    ...     self.l.setdefault(key, []).extend(vals); return len(vals)
    >>> plan = ClosedPathReconstructPlan(
    ...   path="/a", identity="/a|1|2", needs_ingest=True, needs_append=False,
    ...   calendar_day=date(2026, 8, 20), tar_path=None,
    ... )
    >>> enqueue_reconstruct_jobs_for_closed_path(
    ...   _C(), plan, today=date(2026, 8, 24)
    ... )["ingest"]
    True
  """
  enqueued = {"ingest": False, "append": False}
  if client is None or plan is None:
    return enqueued
  root = str(archive_data_dir or "").strip()

  if plan.needs_ingest:
    if root and jq.identity_in_queue_dead_letter(
        root, kind=jq.JOB_KIND_INGEST, identity=plan.identity,
    ):
      pass
    elif plan.calendar_day is None:
      # Refusing to substitute today: an unresolved day cannot be banded.
      pass
    else:
      day = plan.calendar_day
      band = select_ingest_band(day, today=today, hot_days=hot_days)
      score = jq.encode_ingest_score(
          band=band,
          day=day,
          today=today,
          identity=plan.identity,
      )
      jq.zadd_ingest_job(
          client,
          identity=plan.identity,
          score=score,
          fingerprint=plan.fingerprint or None,
      )
      enqueued["ingest"] = True

  if plan.needs_append:
    if root and jq.identity_in_queue_dead_letter(
        root, kind=jq.JOB_KIND_APPEND, identity=plan.path,
    ):
      pass
    else:
      jq.enqueue_list_job(
          client,
          kind=jq.JOB_KIND_APPEND,
          identity=plan.path,
          dedupe=True,
      )
      enqueued["append"] = True

  return enqueued


def enqueue_day_close_if_needed(
  client: Any,
  tar_path: str,
  *,
  calendar_day: date | None = None,
  phase_name: str | None = None,
  filesystem_complete: bool | None = None,
  min_age_elapsed: bool | None = None,
  now: datetime | None = None,
) -> bool:
  """
  ``RPUSH`` a day_close job when filesystem+32h predicates are incomplete.

  Ghost ``phase=done`` does not skip enqueue. Complete identities are not
  enqueued.

  Args:
    client (Any): Redis client with ``rpush``.
    tar_path (str): Daily tar identity / path.
    calendar_day (date | None): Day for min-age when not injected.
    phase_name (str | None): Ignored removal/manifest phase.
    filesystem_complete (bool | None): Injected FS-complete.
    min_age_elapsed (bool | None): Injected min-age.
    now (datetime | None): Clock for min-age.

  Returns:
    bool: True when a day_close identity was enqueued.

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.l = {}
    ...   def rpush(self, key, *vals):
    ...     self.l.setdefault(key, []).extend(vals); return len(vals)
    >>> enqueue_day_close_if_needed(
    ...   _C(),
    ...   "/d/2026-08-01.tar",
    ...   calendar_day=date(2026, 8, 1),
    ...   phase_name="done",
    ...   filesystem_complete=False,
    ...   min_age_elapsed=True,
    ... )
    True
  """
  if client is None:
    return False
  if day_close_is_complete(
      tar_path,
      calendar_day=calendar_day,
      filesystem_complete=filesystem_complete,
      min_age_elapsed=min_age_elapsed,
      now=now,
      phase_name=phase_name,
  ):
    return False
  jq.enqueue_list_job(
      client,
      kind=jq.JOB_KIND_DAY_CLOSE,
      identity=os.path.normpath(str(tar_path)),
      dedupe=True,
  )
  return True


def enqueue_cheap_day_close_if_needed(
  client: Any,
  tar_path: str,
  *,
  calendar_day: date | None = None,
  phase_name: str | None = None,
  min_age_elapsed: bool | None = None,
  now: datetime | None = None,
) -> bool:
  """
  Enqueue day_close without a blocking filesystem remaining-raw find.

  Append/reconstruct coordinators must not call
  ``day_close_filesystem_complete`` (archive-wide find). Inject
  ``filesystem_complete=False`` so Redis LIST dedupe can keep at most one
  queued/inflight identity per tar; day_close **workers** own the full FS
  probe.

  Args:
    client (Any): Redis client with ``rpush``.
    tar_path (str): Daily tar identity / path.
    calendar_day (date | None): Day for min-age when not injected (unused
      while FS is forced incomplete; retained for API parity).
    phase_name (str | None): Ignored removal/manifest phase.
    min_age_elapsed (bool | None): Injected min-age (unused while FS is
      forced incomplete; retained for API parity).
    now (datetime | None): Clock for min-age (unused on cheap path).

  Returns:
    bool: True when a day_close identity was enqueued (or would be after
      dedupe skip still returns True from the complete check path).

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.n = 0
    ...   def rpush(self, key, *vals):
    ...     self.n += len(vals); return self.n
    ...   def eval(self, *a, **k):
    ...     return 1
    ...   def evalsha(self, *a, **k):
    ...     return 1
    ...   def script_load(self, s):
    ...     return "x"
    >>> enqueue_cheap_day_close_if_needed(
    ...   _C(),
    ...   "/d/2026-08-01.tar",
    ...   calendar_day=date(2026, 8, 1),
    ... )
    True
  """
  return enqueue_day_close_if_needed(
      client,
      tar_path,
      calendar_day=calendar_day,
      phase_name=phase_name,
      filesystem_complete=False,
      min_age_elapsed=min_age_elapsed,
      now=now,
  )
