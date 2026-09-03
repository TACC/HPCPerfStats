"""
In-process daily archive member coordination (single-flight populate).

Maps, skip, degraded, tar-hot, restore, and the populate queue live on
SyncTimedbArchiveMembersStore.

Attributes:
  ArchiveDayIngestSkipError: Sticky day-skip exception.
  ArchiveMembersKeys: Day plus identity handle used by callers.
  ArchiveMembersPopulateStalledError: Populate wait exceeded max seconds.
  ArchiveMembersStoreConnectionError: Unused I/O subclass of store failure.
  ArchiveMembersStoreUnavailableError: Populate/lookup contract failure.
  IngestArchiveLookupBudgetExceededError: Retired ingest-budget error.
  _APPEND_INFLIGHT_DEFER_LOG_STATE: Per-day last-emit map for append-inflight
    defer INFO.
  _EMPTY_RECOVER_DEFER_LOG_STATE: Per-day last-emit map for empty-recover
    defer INFO.
  _IDENTITY_DRIFT_LOG_INTERVAL_S: Minimum seconds between identity-drift
    INFO lines.
  _IDENTITY_DRIFT_LOG_STATE: Per-day last-emit map for identity-drift INFO.
  _POPULATE_PREFER_INGEST_HOT_REASONS: Ingest-hot reasons that rank populate
    queue jobs ahead of cold FIFO.
  _RATE_LIMIT_STATE_MAX_DAYS: Cap on per-day rate-limit state rows.
  _RATE_LIMIT_STATE_STALE_S: Drop rate-limit rows idle longer than this.
  _SELF_INGEST_TAR_HOT_REASONS: Tar-hot reasons that count as self-hot
    populate wait.
  _STALE_INCOMPLETE_LOG_INTERVAL_S: Minimum seconds between stale-incomplete
    WARN lines.
  _STALE_INCOMPLETE_LOG_STATE: Per-day last-emit map for stale-incomplete
    WARN.
  _archive_pre_append_member_lookup: ContextVar marking archive-pool
    pre-append lookup.
  _ingest_task_deadline_monotonic: Retired ingest wall-deadline ContextVar.
  _ingest_task_effective_timeout_s: Retired ingest timeout ContextVar.
"""
from __future__ import annotations

import contextvars
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    get_process_archive_members_store,
    require_process_archive_members_store,
)

_IDENTITY_DRIFT_LOG_INTERVAL_S = 120.0
_STALE_INCOMPLETE_LOG_INTERVAL_S = 60.0
_RATE_LIMIT_STATE_MAX_DAYS = 128
_RATE_LIMIT_STATE_STALE_S = 7200.0
_IDENTITY_DRIFT_LOG_STATE: Dict[str, Dict[str, float]] = {}
_APPEND_INFLIGHT_DEFER_LOG_STATE: Dict[str, Dict[str, float]] = {}
_STALE_INCOMPLETE_LOG_STATE: Dict[str, Dict[str, float]] = {}
_EMPTY_RECOVER_DEFER_LOG_STATE: Dict[str, Dict[str, float]] = {}

_archive_pre_append_member_lookup = contextvars.ContextVar(
    "sync_timedb_archive_pre_append_member_lookup",
    default=False,
)
_ingest_task_deadline_monotonic = contextvars.ContextVar(
    "ingest_task_deadline_monotonic",
    default=None,
)
_ingest_task_effective_timeout_s = contextvars.ContextVar(
    "ingest_task_effective_timeout_s",
    default=None,
)

_SELF_INGEST_TAR_HOT_REASONS = frozenset({
    "populate_wait",
    "populate_enqueue",
    "chunk_prewarm",
    "populate",
})
_POPULATE_PREFER_INGEST_HOT_REASONS = frozenset({
    "chunk_prewarm",
    "populate_wait",
    "populate_enqueue",
})


class ArchiveMembersStoreUnavailableError(RuntimeError):
    """Raised when the in-process member-map contract cannot be satisfied."""


class ArchiveMembersStoreConnectionError(ArchiveMembersStoreUnavailableError):
    """Compatibility alias retained for existing exception handlers."""


class ArchiveMembersPopulateStalledError(ArchiveMembersStoreUnavailableError):
    """Raised when populate does not finish within populate_max_seconds."""


class IngestArchiveLookupBudgetExceededError(TimeoutError):
    """Raised when an ingest archive lookup exceeds its per-file budget."""


class ArchiveDayIngestSkipError(RuntimeError):
    """
    Sealed daily archive unreadable; ingest skips tar-append checks.

    Attributes:
      day_token: ISO calendar day.
      detail: Short diagnostic text.
      kind: Skip classification.
      sealed_path: Sealed archive path that failed.
    """

    def __init__(
        self,
        day_token: Any,
        sealed_path: str,
        kind: Any,
        detail: Any,
    ) -> None:
        """
        Record one sticky day-skip failure.

        Args:
          day_token (Any): ISO calendar day.
          sealed_path (str): Sealed archive path.
          kind (Any): Skip classification.
          detail (Any): Diagnostic text.

        Returns:
          None

        Examples:
          >>> ArchiveDayIngestSkipError("2026-01-01", "x", "read_error", "x")
        """
        self.day_token = day_token
        self.sealed_path = sealed_path
        self.kind = kind
        self.detail = detail
        super().__init__(
            "archive day ingest skip day=%s sealed_path=%s kind=%s detail=%s"
            % (day_token, sealed_path, kind, detail),
        )


@dataclass(frozen=True)
class ArchiveMembersKeys:
    """
    Day plus identity handle for one daily archive.

    Attributes:
      complete_key: Compatibility log token.
      day_token: ISO calendar day.
      dedupe_hint_key: Compatibility log token.
      hash_key: Compatibility log token.
      identity: Store identity suffix.
      invalidate_pending_key: Compatibility log token.
      lock_key: Compatibility log token.
    """

    day_token: str
    identity: str
    hash_key: str
    complete_key: str
    lock_key: str
    dedupe_hint_key: str
    invalidate_pending_key: str

    @property
    def progress_key(self) -> str:
        """
        Compatibility progress token.

        Returns:
          str: Log token.

        Examples:
          >>> ArchiveMembersKeys(
          ...   "d", "i", "h", "c", "l", "dh", "ip",
          ... ).progress_key.endswith(":progress")
          True
        """
        return "%s:progress" % self.hash_key

    @property
    def degraded_key(self) -> str:
        """
        Compatibility degraded token.

        Returns:
          str: Log token.

        Examples:
          >>> ArchiveMembersKeys(
          ...   "d", "i", "h", "c", "l", "dh", "ip",
          ... ).degraded_key.endswith("d")
          True
        """
        return "archive_populate_degraded:%s" % self.day_token

    @property
    def day_skip_key(self) -> str:
        """
        Compatibility day-skip token.

        Returns:
          str: Log token.

        Examples:
          >>> ArchiveMembersKeys(
          ...   "d", "i", "h", "c", "l", "dh", "ip",
          ... ).day_skip_key.endswith("d")
          True
        """
        return "archive_day_ingest_skip:%s" % self.day_token


def is_transient_fnctl_populate_unavailable(exc: Any) -> bool:
    """
    True when *exc* is a transient fnctl read-lock timeout during populate.

    Args:
      exc (Any): Exception instance being classified.

    Returns:
      bool: True when the error is a recoverable fnctl timeout.

    Examples:
      >>> is_transient_fnctl_populate_unavailable(RuntimeError("x"))
      False
    """
    if not isinstance(exc, ArchiveMembersStoreUnavailableError):
        return False
    if isinstance(
        exc,
        (ArchiveMembersStoreConnectionError, ArchiveMembersPopulateStalledError),
    ):
        return False
    msg = str(exc).lower()
    if "transient fnctl" in msg and "read lock timeout" in msg:
        return True
    return "timed out waiting" in msg and "fnctl.lock" in msg


def is_populate_pool_unavailable_error(exc: Any) -> bool:
    """
    True when *exc* is populate-pool-down refuse-stream (recoverable).

    Args:
      exc (Any): Exception instance being classified.

    Returns:
      bool: True when ingest must enqueue instead of exiting.

    Examples:
      >>> is_populate_pool_unavailable_error(RuntimeError("x"))
      False
    """
    if not isinstance(exc, ArchiveMembersStoreUnavailableError):
        return False
    if isinstance(
        exc,
        (ArchiveMembersStoreConnectionError, ArchiveMembersPopulateStalledError),
    ):
        return False
    msg = str(exc).lower()
    return "populate-pool unavailable" in msg or "refusing sealed stream" in msg


def set_ingest_task_deadline_monotonic(deadline: Any) -> Any:
    """
    Set monotonic deadline for ingest worker archive lookups.

    Args:
      deadline (Any): Monotonic deadline, or None.

    Returns:
      Any: ContextVar reset token.

    Examples:
      >>> token = set_ingest_task_deadline_monotonic(None)
      >>> reset_ingest_task_deadline_monotonic(token)
    """
    return _ingest_task_deadline_monotonic.set(deadline)


def reset_ingest_task_deadline_monotonic(token: Any) -> None:
    """
    Restore the previous ingest deadline ContextVar.

    Args:
      token (Any): Token from set_ingest_task_deadline_monotonic.

    Returns:
      None

    Examples:
      >>> token = set_ingest_task_deadline_monotonic(1.0)
      >>> reset_ingest_task_deadline_monotonic(token)
    """
    _ingest_task_deadline_monotonic.reset(token)


def get_ingest_task_deadline_monotonic() -> Any:
    """
    Return the ingest task deadline, or None.

    Returns:
      Any: Monotonic deadline.

    Examples:
      >>> get_ingest_task_deadline_monotonic() is None
      True
    """
    return _ingest_task_deadline_monotonic.get()


def extend_ingest_task_deadline_monotonic(delta_seconds: int) -> None:
    """
    Extend the active ingest worker deadline by populate-wait wall time.

    Args:
      delta_seconds (int): Seconds to add.

    Returns:
      None

    Examples:
      >>> extend_ingest_task_deadline_monotonic(0)
    """
    delta_seconds = float(delta_seconds)
    if delta_seconds <= 0.0:
        return
    deadline = get_ingest_task_deadline_monotonic()
    if deadline is None:
        return
    _ingest_task_deadline_monotonic.set(float(deadline) + delta_seconds)


def set_ingest_task_effective_timeout_s(timeout_s: Any) -> Any:
    """
    Store the resolved per-file ingest budget for this worker task.

    Args:
      timeout_s (Any): Seconds, or None.

    Returns:
      Any: ContextVar reset token.

    Examples:
      >>> token = set_ingest_task_effective_timeout_s(None)
      >>> reset_ingest_task_effective_timeout_s(token)
    """
    return _ingest_task_effective_timeout_s.set(timeout_s)


def reset_ingest_task_effective_timeout_s(token: Any) -> None:
    """
    Restore the previous ingest effective-timeout ContextVar.

    Args:
      token (Any): Token from set_ingest_task_effective_timeout_s.

    Returns:
      None

    Examples:
      >>> token = set_ingest_task_effective_timeout_s(1.0)
      >>> reset_ingest_task_effective_timeout_s(token)
    """
    _ingest_task_effective_timeout_s.reset(token)


def get_ingest_task_effective_timeout_s() -> Any:
    """
    Return the ingest effective timeout, or None.

    Returns:
      Any: Seconds.

    Examples:
      >>> get_ingest_task_effective_timeout_s() is None
      True
    """
    return _ingest_task_effective_timeout_s.get()


def _raise_if_ingest_deadline_exceeded() -> None:
    """
    Wall-budget archive-lookup abort — retired (no-op).

    Returns:
      None

    Examples:
      >>> _raise_if_ingest_deadline_exceeded()
    """
    return


def _raise_if_ingest_deadline_exceeded_when_enabled(
    respect_ingest_deadline: Any,
) -> None:
    """
    No-op deadline check retained for caller compatibility.

    Args:
      respect_ingest_deadline (Any): Unused flag.

    Returns:
      None

    Examples:
      >>> _raise_if_ingest_deadline_exceeded_when_enabled(True)
    """
    if respect_ingest_deadline:
        _raise_if_ingest_deadline_exceeded()


def _identity_pair(identity: Any) -> tuple:
    """
    Normalize a sealed or tar identity pair.

    Args:
      identity (Any): ``(mtime, size)`` pair, or None.

    Returns:
      tuple: String pair.

    Examples:
      >>> _identity_pair(None)
      ('none', 'none')
    """
    if identity is None:
        return ("none", "none")
    return (str(int(identity[0])), str(int(identity[1])))


def build_archive_members_keys(cache_key: Any) -> ArchiveMembersKeys:
    """
    Build a day/identity handle from ``_daily_archive_members_cache_key``.

    Args:
      cache_key (Any): ``(canonical, sealed_identity, tar_identity)``.

    Returns:
      ArchiveMembersKeys: Store handle.

    Examples:
      >>> build_archive_members_keys(  # doctest: +SKIP
      ...   ("/d/2026-01-01.tar.zst", None, None),
      ... )
    """
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        calendar_date_from_daily_tar_path,
        daily_tar_path_from_compressed,
    )

    canonical_zst_path, sealed_identity, tar_identity = cache_key
    tar_path = daily_tar_path_from_compressed(canonical_zst_path)
    day_date = calendar_date_from_daily_tar_path(tar_path)
    day_token = day_date.isoformat() if day_date is not None else "unknown"
    sealed_m, sealed_s = _identity_pair(sealed_identity)
    tar_m, tar_s = _identity_pair(tar_identity)
    suffix = "%s:%s:%s:%s:%s" % (day_token, sealed_m, sealed_s, tar_m, tar_s)
    return ArchiveMembersKeys(
        day_token=day_token,
        identity=suffix,
        hash_key="archive_members:%s" % suffix,
        complete_key="archive_members_complete:%s" % suffix,
        lock_key="archive_members_lock:%s" % suffix,
        dedupe_hint_key="archive_dedupe_hint:%s" % day_token,
        invalidate_pending_key="archive_members_invalidate:%s" % suffix,
    )


def populate_wait_max_seconds() -> int:
    """
    Return the configured populate wait cap in seconds.

    Returns:
      int: Seconds (0 means no extra cap beyond populate_max).

    Examples:
      >>> populate_wait_max_seconds() >= 0
      True
    """
    getter = getattr(cfg, "get_sync_archive_members_populate_wait_seconds", None)
    if callable(getter):
        try:
            return max(0, int(getter()))
        except (TypeError, ValueError):
            return 0
    return 0


def _wait_poll_seconds() -> float:
    """
    Return the populate wait poll interval.

    Returns:
      float: Seconds.

    Examples:
      >>> _wait_poll_seconds() > 0
      True
    """
    getter = getattr(cfg, "get_sync_archive_members_wait_poll_seconds", None)
    if callable(getter):
        try:
            return max(0.05, float(getter()))
        except (TypeError, ValueError):
            return 0.25
    return 0.25


def _populate_max_seconds() -> int:
    """
    Return the configured populate max seconds.

    Returns:
      int: Seconds.

    Examples:
      >>> _populate_max_seconds() >= 0
      True
    """
    getter = getattr(cfg, "get_sync_archive_members_populate_max_seconds", None)
    if callable(getter):
        try:
            return max(0, int(getter()))
        except (TypeError, ValueError):
            return 0
    return 0


def archive_pre_append_member_lookup_active() -> bool:
    """
    True when the caller is inside a pre-append member lookup.

    Returns:
      bool: ContextVar flag.

    Examples:
      >>> archive_pre_append_member_lookup_active()
      False
    """
    return bool(_archive_pre_append_member_lookup.get())


class archive_pre_append_member_lookup_context:
    """
    Mark the current task as a pre-append member lookup.

    Attributes:
      _token: ContextVar reset token.
    """

    def __enter__(self) -> "archive_pre_append_member_lookup_context":
        """
        Set the pre-append lookup flag.

        Returns:
          archive_pre_append_member_lookup_context: Self.

        Examples:
          >>> with archive_pre_append_member_lookup_context():
          ...   archive_pre_append_member_lookup_active()
          True
        """
        self._token = _archive_pre_append_member_lookup.set(True)
        return self

    def __exit__(self, *exc: Any) -> None:
        """
        Restore the pre-append lookup flag.

        Args:
          *exc (Any): Exception triple.

        Returns:
          None

        Examples:
          >>> with archive_pre_append_member_lookup_context():
          ...   pass
        """
        _archive_pre_append_member_lookup.reset(self._token)


def populate_degraded_is_set(
    keys: ArchiveMembersKeys,
    client: Any | None = None,
) -> bool:
    """
    Return True when the day is marked populate-degraded.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      client (Any | None): Ignored leftover argument.

    Returns:
      bool: True when degraded.

    Examples:
      >>> populate_degraded_is_set(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ... )
    """
    del client
    store = get_process_archive_members_store()
    if store is None:
        return False
    return store.is_degraded(keys.day_token)


def set_archive_day_ingest_skip(
    keys: ArchiveMembersKeys,
    *,
    kind: str,
    detail: str = "",
    client: Any | None = None,
) -> None:
    """
    Persist a sticky ingest skip for the day on *keys*.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      kind (str): Skip classification.
      detail (str): Diagnostic text.
      client (Any | None): Ignored leftover argument.

    Returns:
      None

    Examples:
      >>> set_archive_day_ingest_skip(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ...   kind="read_error",
      ... )
    """
    del client
    require_process_archive_members_store().set_day_skip(
        keys.day_token, kind=str(kind), detail=str(detail),
    )


def clear_archive_day_ingest_skip(
    keys: ArchiveMembersKeys,
    client: Any | None = None,
) -> None:
    """
    Clear the sticky ingest skip for the day on *keys*.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      client (Any | None): Ignored leftover argument.

    Returns:
      None

    Examples:
      >>> clear_archive_day_ingest_skip(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ... )
    """
    del client
    store = get_process_archive_members_store()
    if store is None:
        return
    store.clear_day_skip(keys.day_token)


def get_archive_day_ingest_skip(
    keys: ArchiveMembersKeys,
    client: Any | None = None,
) -> Optional[Dict[str, str]]:
    """
    Return the sticky skip payload, or None.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      client (Any | None): Ignored leftover argument.

    Returns:
      dict[str, str] | None: kind/detail, or None.

    Examples:
      >>> get_archive_day_ingest_skip(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ... )
    """
    del client
    store = get_process_archive_members_store()
    if store is None:
        return None
    return store.get_day_skip(keys.day_token)


def archive_day_ingest_skip_error_from_store(
    keys: ArchiveMembersKeys,
    sealed_path: str,
    client: Any | None = None,
) -> Optional[ArchiveDayIngestSkipError]:
    """
    Build a skip error when the day is sticky-skipped.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      sealed_path (str): Sealed archive path.
      client (Any | None): Ignored leftover argument.

    Returns:
      ArchiveDayIngestSkipError | None: Error, or None.

    Examples:
      >>> archive_day_ingest_skip_error_from_store(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ...   "x",
      ... )
    """
    skip = get_archive_day_ingest_skip(keys, client=client)
    if skip is None:
        return None
    return ArchiveDayIngestSkipError(
        keys.day_token,
        sealed_path,
        skip.get("kind"),
        skip.get("detail"),
    )


def _raise_if_archive_day_ingest_skip(
    keys: ArchiveMembersKeys,
    sealed_path: str,
    client: Any | None = None,
) -> None:
    """
    Raise ArchiveDayIngestSkipError when the day is sticky-skipped.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      sealed_path (str): Sealed archive path.
      client (Any | None): Ignored leftover argument.

    Returns:
      None

    Raises:
      ArchiveDayIngestSkipError: Raised when the store has a sticky skip
        for the calendar day.
      err: ArchiveDayIngestSkipError instance when skip is set.

    Examples:
      >>> _raise_if_archive_day_ingest_skip(  # doctest: +SKIP
        ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
        ...   "x",
        ... )
    """
    err = archive_day_ingest_skip_error_from_store(
        keys, sealed_path, client=client,
    )
    if err is not None:
        raise err


def lookup_full_members(keys: ArchiveMembersKeys) -> Optional[dict]:
    """
    Return the complete member map, or None when incomplete.

    Args:
      keys (ArchiveMembersKeys): Day handle.

    Returns:
      dict | None: Complete map.

    Examples:
      >>> lookup_full_members(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ... )
    """
    store = get_process_archive_members_store()
    if store is None:
        return None
    return store.lookup_complete_map(keys.day_token, keys.identity)


def members_cache_is_fully_warm(
    keys: ArchiveMembersKeys,
    *,
    client: Any | None = None,
) -> bool:
    """
    True when a complete non-empty member map is in the store.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      client (Any | None): Ignored leftover argument.

    Returns:
      bool: True when warm.

    Examples:
      >>> members_cache_is_fully_warm(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ... )
    """
    del client
    members = lookup_full_members(keys)
    return bool(members)


def member_match_when_warm(
    keys: ArchiveMembersKeys,
    member_name: str,
    expected_size: int,
    *,
    client: Any | None = None,
) -> Optional[bool]:
    """
    Point duplicate-check when the store map is fully warm.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      member_name (str): Archive member name.
      expected_size (int): Expected size.
      client (Any | None): Ignored leftover argument.

    Returns:
      bool | None: True/False when warm, None when cold.

    Examples:
      >>> member_match_when_warm(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ...   "host/1",
      ...   1,
      ... )
    """
    del client
    if not members_cache_is_fully_warm(keys):
        return None
    store = require_process_archive_members_store()
    expected_size = int(expected_size)
    size = store.lookup_member(keys.day_token, keys.identity, member_name)
    if size is not None:
        if int(size) == expected_size:
            return True
        if int(size) > expected_size:
            return False
    if store.is_complete(keys.day_token, keys.identity):
        return False
    return None


def maybe_clear_orphan_incomplete_archive_members(
    keys: ArchiveMembersKeys,
    client: Any | None = None,
) -> None:
    """
    Drop a stale incomplete identity map.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      client (Any | None): Ignored leftover argument.

    Returns:
      None

    Examples:
      >>> maybe_clear_orphan_incomplete_archive_members(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ... )
    """
    del client
    store = get_process_archive_members_store()
    if store is None:
        return
    store.clear_incomplete(keys.day_token, keys.identity)


def clear_stale_incomplete_archive_members(
    keys: ArchiveMembersKeys,
    client: Any | None = None,
) -> None:
    """
    Drop incomplete maps and the degraded flag for one identity.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      client (Any | None): Ignored leftover argument.

    Returns:
      None

    Examples:
      >>> clear_stale_incomplete_archive_members(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ... )
    """
    del client
    store = get_process_archive_members_store()
    if store is None:
        return
    store.clear_incomplete(keys.day_token, keys.identity)
    store.clear_degraded(keys.day_token)


def store_complete_members(
    keys: ArchiveMembersKeys,
    members: dict,
    *,
    saw_duplicates: bool = False,
) -> None:
    """
    Write a complete member map into the process store.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      members (dict): Member name to size map.
      saw_duplicates (bool): True when the tar listed duplicate names.

    Returns:
      None

    Examples:
      >>> store_complete_members(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ...   {},
      ... )
    """
    require_process_archive_members_store().store_complete(
        keys.day_token,
        keys.identity,
        {str(name): int(size) for name, size in (members or {}).items()},
        saw_duplicates=saw_duplicates,
    )


def merge_appended_members(
    cache_key: Any,
    member_map: Any,
    *,
    saw_duplicates: bool = False,
) -> bool:
    """
    Merge appended tar members into the complete store map.

    Args:
      cache_key (Any): Daily archive cache key.
      member_map (Any): Newly appended member sizes.
      saw_duplicates (bool): True when the append listed duplicate names.

    Returns:
      bool: True when a map was updated.

    Examples:
      >>> merge_appended_members(None, {}, True)
      False
    """
    if not member_map:
        return False
    keys = build_archive_members_keys(cache_key)
    return require_process_archive_members_store().merge_members(
        keys.day_token,
        keys.identity,
        {str(name): int(size) for name, size in member_map.items()},
        saw_duplicates=saw_duplicates,
    )


def invalidate_archive_members(cache_key: Any) -> None:
    """
    Drop one identity map from the process store.

    Args:
      cache_key (Any): Daily archive cache key.

    Returns:
      None

    Examples:
      >>> invalidate_archive_members(None)  # doctest: +SKIP
    """
    store = get_process_archive_members_store()
    if store is None:
        return
    keys = build_archive_members_keys(cache_key)
    store.invalidate(keys.day_token, keys.identity)


def invalidate_archive_members_bulk(
    *,
    day_tokens: Any | None = None,
    dry_run: bool = False,
    client: Any | None = None,
) -> dict:
    """
    Drop member sidecars for selected days, or every day when omitted.

    Args:
      day_tokens (Any | None): Calendar days, or None for all.
      dry_run (bool): True to report without mutating.
      client (Any | None): Ignored leftover argument.

    Returns:
      dict: ``{"days": int, "dry_run": bool}``.

    Examples:
      >>> invalidate_archive_members_bulk(dry_run=True)["dry_run"]
      True
    """
    del client
    store = get_process_archive_members_store()
    if store is None or dry_run:
        return {"days": 0, "dry_run": bool(dry_run)}
    if day_tokens:
        count = 0
        for day in day_tokens:
            store.invalidate(str(day), "")
            store.clear_day_skip(str(day))
            store.clear_degraded(str(day))
            count += 1
        return {"days": count, "dry_run": False}
    store.invalidate_all()
    return {"days": 1, "dry_run": False}


def list_dedupe_hint_day_tokens(client: Any | None = None) -> list:
    """
    Return calendar days that currently have a dedupe hint.

    Args:
      client (Any | None): Ignored leftover argument.

    Returns:
      list: Day tokens.

    Examples:
      >>> list_dedupe_hint_day_tokens()
      []
    """
    del client
    store = get_process_archive_members_store()
    if store is None:
        return []
    return store.list_dedupe_hint_days()


def clear_dedupe_hint(day_token: str, client: Any | None = None) -> None:
    """
    Clear the persisted dedupe hint for one calendar day.

    Args:
      day_token (str): ISO calendar day.
      client (Any | None): Ignored leftover argument.

    Returns:
      None

    Examples:
      >>> clear_dedupe_hint("2026-01-01")
    """
    del client
    store = get_process_archive_members_store()
    if store is None:
        return
    store.clear_dedupe_hint(day_token)


def dedupe_hint_is_set(day_token: str, client: Any | None = None) -> bool:
    """
    Return True when the day saw duplicate tar members.

    Args:
      day_token (str): ISO calendar day.
      client (Any | None): Ignored leftover argument.

    Returns:
      bool: True when set.

    Examples:
      >>> dedupe_hint_is_set("2026-01-01")
      False
    """
    del client
    store = get_process_archive_members_store()
    if store is None:
        return False
    return store.dedupe_hint_is_set(day_token)


def set_ingest_tar_hot(day_token: str, *, reason: str = "populate") -> None:
    """
    Mark a calendar day as ingest-tar-hot in memory.

    Args:
      day_token (str): ISO calendar day.
      reason (str): Hot reason.

    Returns:
      None

    Examples:
      >>> set_ingest_tar_hot("2026-01-01", reason="populate")
    """
    if not day_token or day_token == "unknown":
        return
    store = get_process_archive_members_store()
    if store is None:
        return
    store.set_ingest_tar_hot(day_token, reason=reason or "populate")


def clear_ingest_tar_hot(day_token: str) -> None:
    """
    Drop the in-memory ingest-tar-hot flag.

    Args:
      day_token (str): ISO calendar day.

    Returns:
      None

    Examples:
      >>> clear_ingest_tar_hot("2026-01-01")
    """
    if not day_token or day_token == "unknown":
        return
    store = get_process_archive_members_store()
    if store is None:
        return
    store.clear_ingest_tar_hot(day_token)


def ingest_tar_hot_for_day(day_token: str) -> bool:
    """
    Return True when the day is marked ingest-tar-hot.

    Args:
      day_token (str): ISO calendar day.

    Returns:
      bool: True when hot.

    Examples:
      >>> ingest_tar_hot_for_day("2026-01-01")
      False
    """
    store = get_process_archive_members_store()
    if store is None or not day_token:
        return False
    return store.ingest_tar_hot(day_token)


def ingest_tar_hot_reason_for_day(day_token: str) -> str:
    """
    Return the ingest-tar-hot reason, or empty.

    Args:
      day_token (str): ISO calendar day.

    Returns:
      str: Reason string.

    Examples:
      >>> ingest_tar_hot_reason_for_day("2026-01-01")
      ''
    """
    store = get_process_archive_members_store()
    if store is None or not day_token:
        return ""
    return store.ingest_tar_hot_reason(day_token)


def ingest_tar_hot_is_self_populate_only(day_token: str) -> bool:
    """
    True when hot is set solely by populate wait/enqueue/prewarm.

    Args:
      day_token (str): ISO calendar day.

    Returns:
      bool: True when self-hot only.

    Examples:
      >>> ingest_tar_hot_is_self_populate_only("2026-01-01")
      False
    """
    if not day_token or not ingest_tar_hot_for_day(day_token):
        return False
    if archive_append_inflight_for_day(day_token):
        return False
    reason = ingest_tar_hot_reason_for_day(day_token)
    return (not reason) or reason in _SELF_INGEST_TAR_HOT_REASONS


def set_archive_append_inflight(
    day_token: str,
    *,
    reason: str = "archive_job",
) -> None:
    """
    Mark an in-memory append in flight.

    Args:
      day_token (str): ISO calendar day.
      reason (str): Unused compatibility argument.

    Returns:
      None

    Examples:
      >>> set_archive_append_inflight("2026-01-01")
    """
    del reason
    if not day_token or day_token == "unknown":
        return
    store = get_process_archive_members_store()
    if store is None:
        return
    store.set_append_inflight(day_token)


def clear_archive_append_inflight(day_token: str) -> None:
    """
    Drop the in-memory append-inflight flag.

    Args:
      day_token (str): ISO calendar day.

    Returns:
      None

    Examples:
      >>> clear_archive_append_inflight("2026-01-01")
    """
    if not day_token or day_token == "unknown":
        return
    store = get_process_archive_members_store()
    if store is None:
        return
    store.clear_append_inflight(day_token)


def archive_append_inflight_for_day(day_token: str) -> bool:
    """
    Return True when an append is in flight for the day.

    Args:
      day_token (str): ISO calendar day.

    Returns:
      bool: True when in flight.

    Examples:
      >>> archive_append_inflight_for_day("2026-01-01")
      False
    """
    store = get_process_archive_members_store()
    if store is None or not day_token:
        return False
    return store.append_inflight(day_token)


def try_acquire_daily_tar_restore(
    day_token: str,
    *,
    reason: str,
    caller: str,
) -> str:
    """
    Become the in-memory restore owner for one calendar day.

    Args:
      day_token (str): ISO calendar day.
      reason (str): Restore reason.
      caller (str): Caller label.

    Returns:
      str: Owner token, or empty on conflict.

    Examples:
      >>> try_acquire_daily_tar_restore("2026-01-01", reason="x", caller="t")
      ''
    """
    if not day_token or day_token == "unknown":
        return ""
    store = get_process_archive_members_store()
    if store is None:
        return ""
    token = "%s:%s:%s:%s" % (
        reason or "missing_tar",
        caller or "",
        os.getpid(),
        secrets.token_hex(16),
    )
    if not store.try_acquire_restore(day_token, token):
        return ""
    log_print(
        "archive: daily_tar_restore begin day=%s reason=%s caller=%s"
        % (day_token, reason or "missing_tar", caller or ""),
        flush=True,
    )
    return token


def renew_daily_tar_restore_lease(day_token: str, lease_value: str) -> bool:
    """
    Refresh an owned in-memory restore token.

    Args:
      day_token (str): ISO calendar day.
      lease_value (str): Owner token.

    Returns:
      bool: True when still owned.

    Examples:
      >>> renew_daily_tar_restore_lease("2026-01-01", "x")
      False
    """
    if not day_token or not lease_value:
        return False
    store = get_process_archive_members_store()
    if store is None:
        return False
    return store.renew_restore(day_token, lease_value)


def set_daily_tar_restore_in_progress(
    day_token: str,
    *,
    reason: str,
    caller: str,
) -> str:
    """
    Acquire exclusive restore ownership.

    Args:
      day_token (str): ISO calendar day.
      reason (str): Restore reason.
      caller (str): Caller label.

    Returns:
      str: Owner token, or empty.

    Examples:
      >>> set_daily_tar_restore_in_progress(
      ...   "2026-01-01", reason="x", caller="t",
      ... )
      ''
    """
    return try_acquire_daily_tar_restore(
        day_token, reason=reason, caller=caller,
    )


def clear_daily_tar_restore_in_progress(
    day_token: str,
    *,
    token: str = "",
    ok: bool = True,
    reason: str = "",
) -> None:
    """
    Release restore ownership when *token* still matches.

    Args:
      day_token (str): ISO calendar day.
      token (str): Owner token.
      ok (bool): Success flag for the end log.
      reason (str): End reason.

    Returns:
      None

    Examples:
      >>> clear_daily_tar_restore_in_progress("2026-01-01", token="x")
    """
    if not day_token or day_token == "unknown" or not token:
        return
    store = get_process_archive_members_store()
    if store is None:
        return
    if store.restore_reason(day_token) != token:
        return
    store.clear_restore(day_token, token)
    log_print(
        "archive: daily_tar_restore end day=%s ok=%s reason=%s"
        % (day_token, "yes" if ok else "no", reason or "missing_tar"),
        flush=True,
    )
    try:
        from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
            notify_daily_tar_restore_cleared,
        )

        notify_daily_tar_restore_cleared(day_token)
    except Exception:
        pass


def daily_tar_restore_in_progress_for_day(day_token: str) -> bool:
    """
    Return True when restore is owned for the day.

    Args:
      day_token (str): ISO calendar day.

    Returns:
      bool: True when restore is in progress.

    Examples:
      >>> daily_tar_restore_in_progress_for_day("2026-01-01")
      False
    """
    store = get_process_archive_members_store()
    if store is None or not day_token:
        return False
    return store.restore_in_progress(day_token)


def daily_tar_restore_reason_for_day(day_token: str) -> str:
    """
    Return the restore owner token, or empty.

    Args:
      day_token (str): ISO calendar day.

    Returns:
      str: Owner token.

    Examples:
      >>> daily_tar_restore_reason_for_day("2026-01-01")
      ''
    """
    store = get_process_archive_members_store()
    if store is None or not day_token:
        return ""
    return store.restore_reason(day_token)


def wait_for_daily_tar_restore_before_populate(
    target_path: str,
    *,
    log_fn: Any | None = None,
) -> None:
    """
    Block briefly while a restore token is set for the path's calendar day.

    Args:
      target_path (str): Daily tar or sealed path.
      log_fn (Any | None): Optional logger.

    Returns:
      None

    Examples:
      >>> wait_for_daily_tar_restore_before_populate("/tmp/x")
    """
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        calendar_date_from_daily_tar_path,
    )

    day_date = calendar_date_from_daily_tar_path(target_path)
    if day_date is None:
        return
    day_token = day_date.isoformat()
    deadline = time.monotonic() + max(1.0, float(_populate_max_seconds() or 30))
    while daily_tar_restore_in_progress_for_day(day_token):
        if time.monotonic() >= deadline:
            if log_fn is not None:
                log_fn(
                    "WARNING: daily_tar_restore wait timed out day=%s"
                    % day_token,
                    flush=True,
                )
            return
        time.sleep(_wait_poll_seconds())


def _prune_rate_limit_state(
    state_dict: Dict[str, Dict[str, float]],
    now_mono: float,
) -> None:
    """
    Drop stale per-day rate-limit rows so the dict cannot grow unbounded.

    Args:
      state_dict (dict): Per-day last-log state mutated in place.
      now_mono (float): Current monotonic seconds.

    Returns:
      None

    Examples:
      >>> state = {"2026-01-01": {"last_touch_mono": 1.0, "last_log_mono": 1.0}}
      >>> _prune_rate_limit_state(state, 10000.0)
      >>> state
      {}
    """
    stale_before = now_mono - _RATE_LIMIT_STATE_STALE_S
    for day in list(state_dict):
        last_touch = float(state_dict[day].get("last_touch_mono") or 0.0)
        if last_touch < stale_before:
            state_dict.pop(day, None)
    if len(state_dict) <= _RATE_LIMIT_STATE_MAX_DAYS:
        return
    oldest = sorted(
        state_dict.items(),
        key=lambda item: float(item[1].get("last_touch_mono") or 0.0),
    )
    overflow = len(state_dict) - _RATE_LIMIT_STATE_MAX_DAYS
    for day, _fields in oldest[:overflow]:
        state_dict.pop(day, None)


def _rate_limited_day_info_log(
    state_dict: Dict[str, Dict[str, float]],
    day_token: str,
    message: str,
    *,
    interval_s: float,
    log_fn: Any | None = None,
    skip_unknown_day: bool = True,
) -> None:
    """
    Emit *message* at most once per *interval_s* per day (process-local).

    Args:
      state_dict (dict): Per-day last-log state.
      day_token (str): ISO calendar day.
      message (str): Log line.
      interval_s (float): Minimum seconds between lines.
      log_fn (Any | None): Logger.
      skip_unknown_day (bool): Skip empty/unknown days.

    Returns:
      None

    Examples:
      >>> _rate_limited_day_info_log({}, "unknown", "x", interval_s=1.0)
    """
    if log_fn is None:
        log_fn = log_print
    if not day_token or day_token == "unknown":
        if skip_unknown_day:
            return
        log_fn(message, flush=True)
        return
    now_mono = time.monotonic()
    _prune_rate_limit_state(state_dict, now_mono)
    state = state_dict.get(day_token)
    if state is None:
        state = {
            "last_log_mono": 0.0,
            "last_touch_mono": now_mono,
            "suppressed": 0.0,
        }
        state_dict[day_token] = state
    state["last_touch_mono"] = now_mono
    last_log_mono = float(state.get("last_log_mono") or 0.0)
    if now_mono - last_log_mono < float(interval_s):
        state["suppressed"] = float(state.get("suppressed") or 0.0) + 1.0
        return
    suppressed_n = int(state.get("suppressed") or 0)
    state["last_log_mono"] = now_mono
    state["suppressed"] = 0.0
    suffix = (" suppressed_n=%d" % suppressed_n) if suppressed_n else ""
    log_fn("%s%s" % (message, suffix), flush=True)


def _log_append_inflight_defer_if_allowed(day_token: str) -> None:
    """
    Rate-limit the append-inflight defer INFO line.

    Args:
      day_token (str): ISO calendar day.

    Returns:
      None

    Examples:
      >>> _log_append_inflight_defer_if_allowed("2026-01-01")
    """
    _rate_limited_day_info_log(
        _APPEND_INFLIGHT_DEFER_LOG_STATE,
        day_token,
        "INFO: populate deferred while append inflight day=%s" % day_token,
        interval_s=30.0,
    )


def populate_archive_members(
    keys: ArchiveMembersKeys,
    scan_fn: Callable[[Callable[[str, int], None]], tuple],
    *,
    sealed_path: Any | None = None,
    source_decision: Any | None = None,
    scanning_mutable_tar: bool = False,
) -> dict:
    """
    Single-flight populate: ``scan_fn(on_member)`` fills the store map.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      scan_fn (Callable): Scan callback returning readable/duplicates.
      sealed_path (Any | None): Sealed path for skip classification.
      source_decision (Any | None): Optional source-decision log payload.
      scanning_mutable_tar (bool): True when scanning an open tar.

    Returns:
      dict: Complete member map.

    Raises:
      ArchiveMembersPopulateStalledError: Populate exceeded max seconds.
      ArchiveDayIngestSkipError: Sticky skip after an unreadable scan.
      Exception: Re-raised after marking the day populate-degraded.

    Examples:
      >>> populate_archive_members(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ...   lambda on_member: (True, False),
      ... )
    """
    del scanning_mutable_tar
    store = require_process_archive_members_store()
    started = time.monotonic()
    max_seconds = _populate_max_seconds()
    while True:
        if max_seconds > 0 and (time.monotonic() - started) >= max_seconds:
            raise ArchiveMembersPopulateStalledError(
                "Timed out waiting for archive members populate (max_seconds=%s): %s"
                % (max_seconds, keys.hash_key),
            )
        existing = store.lookup_complete_map(keys.day_token, keys.identity)
        if existing is not None:
            return existing
        if keys.day_token and archive_append_inflight_for_day(keys.day_token):
            _log_append_inflight_defer_if_allowed(keys.day_token)
            time.sleep(_wait_poll_seconds())
            continue
        if not store.try_begin_populate(keys.day_token, keys.identity):
            waited = store.wait_for_complete(
                keys.day_token,
                keys.identity,
                timeout_s=min(1.0, _wait_poll_seconds() * 4),
            )
            if waited is not None:
                return waited
            continue
        if source_decision is not None:
            from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
                _log_populate_source_decision,
            )
            _log_populate_source_decision(
                source_decision.get("day_token", keys.day_token),
                source_decision.get("tar_path", ""),
                source_decision.get("zst_path", ""),
                source_decision.get("gz_path", ""),
                source_decision.get("sealed_path") or "",
            )
        running_max: Dict[str, int] = {}
        saw_duplicates = False
        seen_in_stream: set[str] = set()

        def _on_member(name: str, size: int) -> None:
            """
            Collect one scanned member size.

            Args:
              name (str): Member name.
              size (int): Member size.

            Returns:
              None

            Examples:
              >>> _on_member("a", 1)
            """
            nonlocal saw_duplicates
            size_i = int(size)
            if name in seen_in_stream:
                saw_duplicates = True
            seen_in_stream.add(name)
            prev = running_max.get(name)
            if prev is None or size_i > prev:
                running_max[name] = size_i

        populate_failed = False
        try:
            scan_result = scan_fn(_on_member)
            stream_error = None
            if len(scan_result) == 3:
                readable, scan_duplicates, stream_error = scan_result
            else:
                readable, scan_duplicates = scan_result
            if not readable:
                from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
                    mark_archive_day_ingest_skip_and_raise,
                )
                mark_archive_day_ingest_skip_and_raise(
                    sealed_path or "", keys, None, stream_error,
                )
            if scan_duplicates:
                saw_duplicates = True
            store.store_complete(
                keys.day_token,
                keys.identity,
                dict(running_max),
                saw_duplicates=saw_duplicates,
            )
            store.finish_populate(
                keys.day_token,
                keys.identity,
                members=dict(running_max),
                complete=True,
            )
            return dict(running_max)
        except Exception:
            populate_failed = True
            raise
        finally:
            if populate_failed:
                store.set_degraded(keys.day_token)
                store.finish_populate(
                    keys.day_token, keys.identity, complete=False,
                )


def wait_for_complete_members(
    keys: ArchiveMembersKeys,
    *,
    sealed_path: str = "",
    respect_ingest_deadline: bool = True,
    canonical: str = "",
) -> Optional[dict]:
    """
    Block until the store publishes a complete map or a sticky skip.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      sealed_path (str): Sealed path for skip errors.
      respect_ingest_deadline (bool): Unused leftover flag.
      canonical (str): Unused leftover argument.

    Returns:
      dict | None: Complete map, or None on timeout.

    Examples:
      >>> wait_for_complete_members(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ...   timeout_s=0.01,
      ... )
    """
    del canonical
    _raise_if_ingest_deadline_exceeded_when_enabled(respect_ingest_deadline)
    _raise_if_archive_day_ingest_skip(keys, sealed_path)
    store = require_process_archive_members_store()
    timeout_s = float(_populate_max_seconds() or populate_wait_max_seconds() or 30)
    members = store.wait_for_complete(
        keys.day_token, keys.identity, timeout_s=timeout_s,
    )
    _raise_if_archive_day_ingest_skip(keys, sealed_path)
    return members


def wait_for_member_match(
    keys: ArchiveMembersKeys,
    member_name: str,
    expected_size: int,
    *,
    sealed_path: str = "",
    respect_ingest_deadline: bool = True,
    canonical: str = "",
) -> bool:
    """
    Wait for populate completion, then compare one member size.

    Args:
      keys (ArchiveMembersKeys): Day handle.
      member_name (str): Archive member name.
      expected_size (int): Expected size.
      sealed_path (str): Sealed path for skip errors.
      respect_ingest_deadline (bool): Unused leftover flag.
      canonical (str): Unused leftover argument.

    Returns:
      bool: True when the warm map matches *expected_size*.

    Examples:
      >>> wait_for_member_match(  # doctest: +SKIP
      ...   ArchiveMembersKeys("d", "i", "h", "c", "l", "dh", "ip"),
      ...   "host/1",
      ...   1,
      ... )
    """
    del canonical
    warm = member_match_when_warm(keys, member_name, expected_size)
    if warm is not None:
        return bool(warm)
    members = wait_for_complete_members(
        keys,
        sealed_path=sealed_path,
        respect_ingest_deadline=respect_ingest_deadline,
    )
    if members is None:
        return False
    size = members.get(member_name)
    if size is None:
        return False
    return int(size) == int(expected_size)


def describe_archive_members_populate_for_day(
    day_token: str,
    tgz_archive_dir: str = "",
) -> str:
    """
    Return a short census string for operator logs.

    Args:
      day_token (str): ISO calendar day.
      tgz_archive_dir (str): Unused leftover argument.

    Returns:
      str: Census text.

    Examples:
      >>> "complete" in describe_archive_members_populate_for_day(
      ...   "2026-01-01",
      ... )
      True
    """
    del tgz_archive_dir
    store = get_process_archive_members_store()
    if store is None:
        return "store=unset"
    complete = 0
    with store._lock:
        for (day, _identity), flag in store._complete.items():
            if day == day_token and flag:
                complete += 1
    return "complete_identities=%d degraded=%s skip=%s" % (
        complete,
        "yes" if store.is_degraded(day_token) else "no",
        "yes" if store.get_day_skip(day_token) else "no",
    )


def archive_members_populate_shows_progress_for_day(
    day_token: str,
    tgz_archive_dir: str = "",
    *,
    progress_state: Any | None = None,
) -> bool:
    """
    True when a populate owner or complete map exists for the day.

    Args:
      day_token (str): ISO calendar day.
      tgz_archive_dir (str): Unused leftover argument.
      progress_state (Any | None): Unused leftover argument.

    Returns:
      bool: True when work is visible.

    Examples:
      >>> archive_members_populate_shows_progress_for_day("2026-01-01")
      False
    """
    del tgz_archive_dir, progress_state
    store = get_process_archive_members_store()
    if store is None:
        return False
    with store._lock:
        for (day, _identity) in store._populate_owner:
            if day == day_token:
                return True
        for (day, _identity), flag in store._complete.items():
            if day == day_token and flag:
                return True
    return False


def enqueue_archive_members_populate(
    canonical_path: str,
    day_token: Any,
) -> bool:
    """
    Enqueue one calendar day for populate-pool workers.

    Args:
      canonical_path (str): Canonical daily archive path.
      day_token (Any): ISO calendar day.

    Returns:
      bool: True when newly queued.

    Examples:
      >>> enqueue_archive_members_populate("x", "")
      False
    """
    if not day_token or day_token == "unknown":
        return False
    set_ingest_tar_hot(str(day_token), reason="populate_enqueue")
    store = get_process_archive_members_store()
    if store is None:
        return False
    return store.enqueue_populate({
        "canonical": str(canonical_path),
        "day_token": str(day_token),
    })


def complete_populate_queue_job(job: Any) -> None:
    """
    ACK a finished populate job.

    Args:
      job (Any): Job dict with day_token.

    Returns:
      None

    Examples:
      >>> complete_populate_queue_job({"day_token": "2026-01-01"})
    """
    store = get_process_archive_members_store()
    if store is None:
        return
    store.complete_populate_job(job)


def requeue_populate_queue_job(job: Any) -> None:
    """
    Return a failed populate job to the in-process queue.

    Args:
      job (Any): Job dict.

    Returns:
      None

    Examples:
      >>> requeue_populate_queue_job({"day_token": "2026-01-01"})
    """
    store = get_process_archive_members_store()
    if store is None:
        return
    store.requeue_populate_job(job)


def archive_members_populate_queue_claim(*, timeout_s: float = 1.0) -> Any:
    """
    Blocking pop for populate-pool workers.

    Args:
      timeout_s (float): Seconds to block.

    Returns:
      Any: Job dict, or None.

    Examples:
      >>> archive_members_populate_queue_claim(timeout_s=0.01) is None
      True
    """
    store = get_process_archive_members_store()
    if store is None:
        time.sleep(min(0.05, max(0.0, float(timeout_s))))
        return None
    return store.dequeue_populate(timeout_s=timeout_s)


def _ensure_populate_pool_running_for_enqueue() -> Any:
    """
    Best-effort MainThread ensure/restart before enqueue.

    Returns:
      Any: Controller, or None.

    Examples:
      >>> _ensure_populate_pool_running_for_enqueue() is None
      True
    """
    from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
        get_populate_pool_controller,
    )

    controller = get_populate_pool_controller()
    if controller is None:
        return None
    if not controller.is_running():
        try:
            controller.reap_and_restart()
        except Exception as exc:
            log_print(
                "WARNING: populate-pool ensure/restart failed: %s" % exc,
                flush=True,
            )
    return controller


def _enqueue_or_run_archive_members_populate(
    canonical: Any,
    day_token: Any,
) -> None:
    """
    Enqueue populate-pool work, or run inline when the pool is unset.

    Args:
      canonical (Any): Canonical daily archive path.
      day_token (Any): ISO calendar day.

    Returns:
      None

    Examples:
      >>> _enqueue_or_run_archive_members_populate("x", "")
    """
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        execute_archive_members_populate_for_canonical,
    )
    from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
        get_worker_pool_kind,
    )

    kind = get_worker_pool_kind()
    if kind in ("ingest-pool", "archive-pool"):
        enqueue_archive_members_populate(canonical, day_token)
        return
    controller = _ensure_populate_pool_running_for_enqueue()
    if controller is not None and controller.is_running():
        enqueue_archive_members_populate(canonical, day_token)
        return
    execute_archive_members_populate_for_canonical(canonical)


def request_archive_members_populate_and_wait(
    archive_compressed_path: str,
) -> Any:
    """
    Wait for a warm member map; enqueue populate-pool work when cold.

    Args:
      archive_compressed_path (str): Canonical daily archive path.

    Returns:
      Any: Complete member map.

    Raises:
      ArchiveMembersStoreUnavailableError: Lookup did not return members.

    Examples:
      >>> request_archive_members_populate_and_wait("x")  # doctest: +SKIP
    """
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        _daily_archive_members_cache_key,
        _lookup_daily_archive_members_cache,
        _resolve_sealed_daily_archive_path,
        _store_daily_archive_members_cache,
        daily_archive_populate_source_exists,
        normalize_daily_compressed_path,
    )

    canonical = normalize_daily_compressed_path(archive_compressed_path)
    if not daily_archive_populate_source_exists(canonical):
        empty: dict = {}
        _store_daily_archive_members_cache(canonical, empty)
        return dict(empty)
    cache_key = _daily_archive_members_cache_key(canonical)
    keys = build_archive_members_keys(cache_key)
    day_token = keys.day_token if keys.day_token != "unknown" else ""
    if day_token:
        from hpcperfstats.dbload.lib.archive_compress import (
            daily_tar_path_from_compressed,
        )
        tar_path = daily_tar_path_from_compressed(canonical)
        set_ingest_tar_hot(day_token, reason="populate_wait")
        wait_for_daily_tar_restore_before_populate(
            tar_path or canonical, log_fn=log_print,
        )
    try:
        cached = _lookup_daily_archive_members_cache(canonical)
        if cached and members_cache_is_fully_warm(keys):
            return dict(cached)
        members = lookup_full_members(keys)
        if members is not None:
            _store_daily_archive_members_cache(canonical, members)
            return dict(members)
        sealed_path = (
            _resolve_sealed_daily_archive_path(archive_compressed_path) or ""
        )
        if populate_degraded_is_set(keys):
            if get_archive_day_ingest_skip(keys) is not None:
                return {}
            clear_stale_incomplete_archive_members(keys)
        _enqueue_or_run_archive_members_populate(canonical, keys.day_token)
        members = wait_for_complete_members(
            keys,
            sealed_path=sealed_path,
            respect_ingest_deadline=False,
            canonical=canonical,
        )
        if members is not None:
            _store_daily_archive_members_cache(canonical, members)
            return dict(members)
        raise ArchiveMembersStoreUnavailableError(
            "archive members lookup did not return members for %s" % canonical,
        )
    finally:
        if day_token:
            clear_ingest_tar_hot(day_token)


def idle_pool_recover_skip_reason_for_paths(
    paths: Any,
    tgz_archive_dir: str = "",
) -> str:
    """
    Compatibility no-op: in-process recover never uses a skip key.

    Args:
      paths (Any): Unused paths.
      tgz_archive_dir (str): Unused archive dir.

    Returns:
      str: Empty string.

    Examples:
      >>> idle_pool_recover_skip_reason_for_paths([])
      ''
    """
    del paths, tgz_archive_dir
    return ""
