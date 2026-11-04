"""
Job-scoped ``host_data`` filter for shared-node co-tenant isolation.

Analysis aggregates must match ``LiveJidScopedDistinctHostTimeCount`` and
ingest stamps: rows belonging to a job have ``jid = <jid>`` (exclusive /
sole) or ``dev = 'j:' || <jid>`` (shared non-GPU / ``host_mem_job``).
GPU hardware ``dev`` stays numeric; optional ``job_affinity.gpu_list``
restricts which devices count for a job.

On shared nodes, residual (non-allowlisted) ``host_data.type`` values are
read host-wide (``host_list`` + time only). Job Detail red help footers
apply only to those residual / host-wide surfaces.

Attributes:
  SHARED_HOST_WIDE_HELP_FOOTER: User-facing red-footer copy for residual
    host-wide counters on shared jobs.
  HOST_WIDE_RESIDUAL_CACHE_REV: Valkey day-frame cache revision.
  MAX_SANE_COUNTER_DELTA: Per-sample ``delta`` ceiling below uint64 wrap.
  MAX_SANE_COUNTER_RATE: Per-sample ``arc`` / ``value`` ceiling.
  _GPU_HOST_DATA_TYPES: Canonical GPU ``host_data.type`` names on the
    job-scoped allowlist.
  _logger: Module logger (exclusive-host fallback warnings).

Exclusive allocations: on a host the job holds alone (no ``job_affinity``
rows, no overlapping ``job_data``), untagged rows in the window also belong
to the job (``job_exclusive_hosts`` / ``job_host_data_scope_sql_params``),
so allocated-but-idle time counts. Ingest still never stamps them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from django.db.models import Q

_logger: logging.Logger = logging.getLogger(__name__)

SHARED_HOST_WIDE_HELP_FOOTER: str = (
  "This counter is node-wide (host-level) on a shared node — it is not "
  "disambiguated to this job. Job-scoped CPU, GPU, and cgroup memory use "
  "separate surfaces without this disclaimer."
)

HOST_WIDE_RESIDUAL_CACHE_REV: int = 1

_GPU_HOST_DATA_TYPES: frozenset[str] = frozenset(
  {"nvidia_gpu", "amd_gpu", "intel_gpu"}
)


def job_dev_token(jid: str) -> str:
  """
  Return the ``host_data.dev`` stamp for a shared-node job id.

  Matches ingest ``shared_dev_for_row`` / ``job_dev_tokens`` truncation.

  Args:
    jid (str): Job id (accounting ``job_data.jid``).

  Returns:
    str: ``j:<jid>`` truncated to 64 characters.

  Examples:
    >>> job_dev_token("42")
    'j:42'
    >>> len(job_dev_token("x" * 80))
    64
  """
  return f"j:{jid!s}"[:64]


def job_host_data_scope_q(jid: str) -> Q:
  """
  Django ``Q`` matching job-owned ``host_data`` rows for one jid.

  Combines exclusive/sole rows (``jid`` column) with shared stamped rows
  (``dev = j:<jid>``). Combine with ``host__in`` and time filters in the
  same queryset; do not use alone for host-wide admin paths.

  Args:
    jid (str): Job id.

  Returns:
    Q: ``jid=<jid> OR dev=j:<jid>``.

  Examples:
    >>> str(job_host_data_scope_q("7"))
    "(OR: ('jid', '7'), ('dev', 'j:7'))"
  """
  jid_s = str(jid)
  return Q(jid=jid_s) | Q(dev=job_dev_token(jid_s))


def job_host_data_scope_sql(
  *,
  table_alias: str = "h",
  jid_param_placeholder: str = "%s",
) -> str:
  """
  SQL fragment ``(alias.jid = %s OR alias.dev = ('j:' || %s))``.

  Callers bind the same jid string twice (or use a single named param in
  dialects that support it). Empty alias uses bare column names.

  Args:
    table_alias (str): Table/alias prefix (default ``h``); empty for bare
      ``jid`` / ``dev``.
    jid_param_placeholder (str): Placeholder text for each bind (default
      ``%s``).

  Returns:
    str: Parenthesized SQL predicate.

  Examples:
    >>> job_host_data_scope_sql()
    "(h.jid = %s OR h.dev = ('j:' || %s))"
    >>> job_host_data_scope_sql(table_alias="")
    "(jid = %s OR dev = ('j:' || %s))"
  """
  prefix = f"{table_alias}." if table_alias else ""
  p = jid_param_placeholder
  return f"({prefix}jid = {p} OR {prefix}dev = ('j:' || {p}))"


def job_host_data_scope_sql_params(
  jid: str,
  *,
  table_alias: str = "h",
  coalesce: bool = True,
  exclusive_hosts: Any = (),
) -> tuple[str, list[Any]]:
  """
  The one bind-param job predicate for raw SQL ``host_data`` reads.

  ``coalesce=True`` wraps ``jid`` / ``dev`` in ``COALESCE`` (same rows) so
  the planner keeps the ``(host, time)`` index walk instead of the jid /
  ``j:`` indexes (hs04 COPY and probe timeouts). ``exclusive_hosts`` adds
  the exclusive-allocation arm: untagged rows (``jid`` NULL, ``''`` or
  ``'-'`` and no ``j:`` stamp) on hosts the job holds alone (see
  ``job_exclusive_hosts``). An empty set returns today's strict predicate
  byte for byte.

  Args:
    jid (str): Job id.
    table_alias (str): ``host_data`` alias (empty for bare columns).
    coalesce (bool): Wrap the jid / dev comparisons in ``COALESCE``.
    exclusive_hosts (Any): Iterable of host FQDNs for the untagged arm;
      callers pass only the hosts of the current query batch.

  Returns:
    tuple[str, list[Any]]: Parenthesized predicate and its parameters
    (jid twice, then the sorted host list when the arm is present).

  Examples:
    >>> job_host_data_scope_sql_params("7")[1]
    ['7', '7']
    >>> job_host_data_scope_sql_params("7", exclusive_hosts=["b", "a"])[1]
    ['7', '7', ['a', 'b']]
  """
  prefix = f"{table_alias}." if table_alias else ""
  jid_s = str(jid)
  if coalesce:
    strict = (
      f"COALESCE({prefix}jid, '') = %s OR "
      f"COALESCE({prefix}dev, '') = ('j:' || %s)"
    )
  else:
    strict = job_host_data_scope_sql(table_alias=table_alias)[1:-1]
  hosts = sorted({str(h) for h in exclusive_hosts or () if h})
  if not hosts:
    return f"({strict})", [jid_s, jid_s]
  untagged = (
    f"({prefix}host = ANY(%s::text[]) AND COALESCE({prefix}jid, '') IN "
    f"('', '-') AND COALESCE({prefix}dev, '') NOT LIKE 'j:%%')"
  )
  return f"({strict} OR {untagged})", [jid_s, jid_s, hosts]


def exclusive_hosts_cache_token(exclusive_hosts: Any) -> str:
  """
  Short, order-free cache-key token for a job's exclusive host set.

  Cached host lists / schemas depend on the scope, so their keys carry this
  token; a change in co-tenancy (a late accounting row) picks a new key.

  Args:
    exclusive_hosts (Any): Iterable of host names.

  Returns:
    str: ``xh-`` when empty, else ``xh`` plus 12 hex digits.

  Examples:
    >>> exclusive_hosts_cache_token([])
    'xh-'
    >>> len(exclusive_hosts_cache_token(["a", "b"]))
    14
  """
  import hashlib

  hosts = sorted({str(h) for h in exclusive_hosts or () if h})
  if not hosts:
    return "xh-"
  digest = hashlib.sha1(",".join(hosts).encode(), usedforsecurity=False)
  return "xh" + digest.hexdigest()[:12]


def job_exclusive_hosts(
  jid: str,
  hosts: Any,
  start: Any,
  end: Any,
  *,
  shared: bool,
  normalize: Callable[[Any], list[str]] | None = None,
  to_host_data: Callable[[str], str] | None = None,
) -> list[str]:
  """
  Accounting hosts on which the job's untagged ``host_data`` rows count.

  A host qualifies when the job is not a shared-node job (no
  ``job_affinity`` rows: ``shared`` False) and no other job is known to
  overlap the window on it. Two sources, neither a ``host_data`` probe:

  * ``job_data`` (accounting): another job on the host whose window
    overlaps strictly (``start_time < end`` and ``end_time > start``), so a
    job that only touches an edge does not block. sacct ingest only lists
    ended jobs, so a co-tenant still running is not here yet.
  * ``job_affinity`` (live, written by listend from ``%affinity`` marks):
    another jid reported on the host at or after our start. When that jid
    has accounting, its window decides; without accounting (running or not
    yet ingested) it blocks the host (fail closed).

  When a co-tenant's accounting row lands later, sync_acct stamps this job
  ``metrics_dirty_at`` (``mark_overlapping_jobs_metrics_dirty``), so a
  stored attribution is recomputed. On a database error the set is empty
  and the job keeps today's strict scope.

  Args:
    jid (str): Job id.
    hosts (Any): The job's accounting host names (``job_data.host_list``).
    start (Any): Window start.
    end (Any): Window end.
    shared (bool): ``job_is_shared_node(jid)``.
    normalize (Callable[[Any], list[str]] | None): Maps one raw
      ``host_list`` value to host names comparable with ``hosts``; default
      strips each entry.
    to_host_data (Callable[[str], str] | None): Maps an accounting host
      name to the ``host_data`` / ``job_affinity`` host (FQDN); default
      identity.

  Returns:
    list[str]: Subset of ``hosts`` (input order) held by this job alone.

  Examples:
    >>> job_exclusive_hosts("7", ["a"], None, None, shared=False)
    []
  """
  host_list = [str(h) for h in hosts or () if h]
  if shared or not str(jid or "").strip() or not host_list:
    return []
  if start is None or end is None:
    return []
  from django.db import DatabaseError

  from hpcperfstats.site.lib.machine.models import job_affinity, job_data

  norm = normalize or (lambda raw: [str(h).strip() for h in raw or ()])
  fqdn = {h: (to_host_data or str)(h) for h in host_list}
  try:
    others = (
      job_data.objects.filter(
        host_list__overlap=host_list,
        start_time__lt=end,
        end_time__gt=start,
      )
      .exclude(jid=str(jid))
      .values_list("host_list", flat=True)
    )
    busy = {h for raw in others for h in norm(raw)}
    live = list(
      job_affinity.objects.filter(
        host__in=sorted(set(fqdn.values())), reported_at__gte=start
      )
      .exclude(jid=str(jid))
      .values_list("jid", "host")
    )
    windows = dict.fromkeys({j for j, _h in live})
    for j, st, et in job_data.objects.filter(jid__in=list(windows)).values_list(
      "jid", "start_time", "end_time"
    ):
      windows[j] = (st, et)
    live_busy = {
      h
      for j, h in live
      if windows[j] is None or (windows[j][0] < end and windows[j][1] > start)
    }
  except DatabaseError as exc:
    _logger.warning(
      "job_exclusive_hosts jid=%s hosts=%d failed (%s: %s); "
      "using strict job scope",
      jid,
      len(host_list),
      type(exc).__name__,
      str(exc)[:200],
    )
    return []
  return [h for h in host_list if h not in busy and fqdn[h] not in live_busy]


def gpu_devs_for_job(
  jid: str,
  host: str | None = None,
) -> list[str] | None:
  """
  Hardware GPU ``dev`` values from ``job_affinity``, or ``None`` if unrestricted.

  When any affinity row exists for the jid (optionally narrowed to ``host``),
  return the union of ``gpu_list`` entries as strings. An empty list means
  the job has affinity but no GPUs (exclude all hardware GPU devices).
  When no affinity rows exist, return ``None`` (job owns the node / no
  restriction — keep all GPU ``dev`` values in the window).

  Args:
    jid (str): Job id.
    host (str | None): Optional hostname; when set, only that host's
      affinity row(s) are read.

  Returns:
    list[str] | None: Allowed ``dev`` strings, empty list, or ``None``.

  Examples:
    >>> gpu_devs_for_job("")  # doctest: +SKIP
    None
  """
  jid_s = str(jid or "").strip()
  if not jid_s:
    return None
  from hpcperfstats.site.lib.machine.models import job_affinity

  qs = job_affinity.objects.filter(jid=jid_s)
  if host:
    qs = qs.filter(host=str(host))
  lists = list(qs.values_list("gpu_list", flat=True))
  if not lists:
    return None
  out: list[str] = []
  seen: set[str] = set()
  for gl in lists:
    if not gl:
      continue
    for d in gl:
      s = str(d).strip()
      if not s or s in seen:
        continue
      seen.add(s)
      out.append(s)
  return out


def gpu_devs_by_host_for_job(jid: str) -> dict[str, list[str]] | None:
  """
  Per-host ``job_affinity.gpu_list`` for one job, or ``None`` without rows.

  Each host is scoped by its own row: a host with no affinity row for the
  job is absent from the map and keeps every GPU (the monitor wrote it
  unmarked, so the job held it alone). Merging all hosts into one list hid
  an unmarked 4-GPU host behind other hosts' empty lists (jid 79282).

  Args:
    jid (str): Job id.

  Returns:
    dict[str, list[str]] | None: ``{host: allowed GPU devs}`` (an empty list
    means no GPUs on that host), or ``None`` when the job has no rows.

  Examples:
    >>> gpu_devs_by_host_for_job("") is None
    True
  """
  jid_s = str(jid or "").strip()
  if not jid_s:
    return None
  from hpcperfstats.site.lib.machine.models import job_affinity

  rows = job_affinity.objects.filter(jid=jid_s).values_list("host", "gpu_list")
  out = {
    str(host): [str(d).strip() for d in gl or () if str(d).strip()]
    for host, gl in rows
  }
  return out or None


def apply_job_host_data_scope(qs: Any, jid: str) -> Any:
  """
  Filter a ``host_data`` queryset to one job's rows.

  Args:
    qs (Any): Django queryset on ``host_data`` (or compatible).
    jid (str): Job id.

  Returns:
    Any: ``qs.filter(job_host_data_scope_q(jid))``.

  Examples:
    >>> apply_job_host_data_scope(None, "1")  # doctest: +SKIP
  """
  if not jid:
    return qs
  return qs.filter(job_host_data_scope_q(str(jid)))


@lru_cache(maxsize=1)
def _job_scoped_host_data_types() -> frozenset[str]:
  """
  Allowlist of ``host_data.type`` names that stay job-scoped on shared nodes.

  Returns:
    frozenset[str]: CPU family, GPU, ``host_mem_job``, core CPU-PMC types.

  Examples:
    >>> "host_cpu" in _job_scoped_host_data_types()
    True
  """
  from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    core_pmc_types_probe_order,
    host_cpu_hw_type_names,
    host_cpu_type_names,
    host_mem_job_type_names,
  )

  names: set[str] = set(_GPU_HOST_DATA_TYPES)
  names.update(host_cpu_type_names())
  names.update(host_cpu_hw_type_names())
  names.update(host_mem_job_type_names())
  names.update(core_pmc_types_probe_order())
  names.add("amd_x86_pmc")
  return frozenset(names)


def is_job_scoped_host_data_type(typ: str | None) -> bool:
  """
  True when ``typ`` is on the shared-node job-scoped allowlist.

  Unknown / residual types return False (host-wide read when shared).

  Args:
    typ (str | None): ``host_data.type`` name.

  Returns:
    bool: Allowlist membership.

  Examples:
    >>> is_job_scoped_host_data_type("host_cpu")
    True
    >>> is_job_scoped_host_data_type("host_mem")
    False
  """
  if typ is None:
    return False
  s = str(typ).strip()
  if not s:
    return False
  return s in _job_scoped_host_data_types()


def use_host_wide_residual_scope(
  typ: str | None,
  *,
  shared: bool,
) -> bool:
  """
  True when shared-node residual types should skip jid / ``j:`` filters.

  Args:
    typ (str | None): ``host_data.type`` (required for residual branch).
    shared (bool): ``job_is_shared_node`` for the job.

  Returns:
    bool: Host-wide residual path.

  Examples:
    >>> use_host_wide_residual_scope("host_mem", shared=True)
    True
    >>> use_host_wide_residual_scope("host_cpu", shared=True)
    False
  """
  return bool(shared) and bool(typ) and not is_job_scoped_host_data_type(typ)


def apply_job_or_host_wide_scope(
  qs: Any,
  jid: str,
  typ: str | None = None,
  *,
  shared: bool = False,
) -> Any:
  """
  Job-scope ``qs``, or leave host/time-only for residual types on shared jobs.

  When ``typ`` is missing, always job-scope (fail closed for untyped dumps).

  Args:
    qs (Any): Django queryset already filtered by host/time (and usually type).
    jid (str): Job id.
    typ (str | None): ``host_data.type`` when known.
    shared (bool): Shared-node signal for this job.

  Returns:
    Any: Scoped or passthrough queryset.

  Examples:
    >>> apply_job_or_host_wide_scope(None, "1", "host_mem", shared=True)
    ... # doctest: +SKIP
  """
  if use_host_wide_residual_scope(typ, shared=shared):
    return qs
  return apply_job_host_data_scope(qs, jid)


def show_shared_host_wide_help_footer(
  *,
  is_shared_node: bool,
  typ: str | None = None,
  surface_job_scoped: bool | None = None,
) -> bool:
  """
  Gate the red shared-node help footer to residual / host-wide surfaces.

  Args:
    is_shared_node (bool): Affinity-based shared-node signal.
    typ (str | None): ``host_data.type`` when the surface maps to one type.
    surface_job_scoped (bool | None): Explicit override when type is unknown
      (True → omit footer; False → show when shared; None → use ``typ``).

  Returns:
    bool: Whether to append ``SHARED_HOST_WIDE_HELP_FOOTER``.

  Examples:
    >>> show_shared_host_wide_help_footer(is_shared_node=True, typ="host_cpu")
    False
    >>> show_shared_host_wide_help_footer(is_shared_node=True, typ="host_mem")
    True
  """
  if not is_shared_node:
    return False
  if surface_job_scoped is True:
    return False
  if surface_job_scoped is False:
    return True
  if typ is None:
    return True
  return not is_job_scoped_host_data_type(typ)


def host_wide_residual_ht_cache_key(
  host_fingerprint: str,
  typ: str,
  day_iso: str,
) -> str:
  """
  Valkey key for residual host-by-time day frames (no jid).

  Args:
    host_fingerprint (str): Stable sorted-host fingerprint.
    typ (str): ``host_data.type``.
    day_iso (str): Calendar day ``YYYY-MM-DD``.

  Returns:
    str: Cache key under ``KEY_HOST_WIDE_RESIDUAL_HT``.

  Examples:
    >>> "host_wide_residual_ht" in host_wide_residual_ht_cache_key(
    ...   "h", "host_mem", "2026-10-22"
    ... )
    True
  """
  from hpcperfstats.site.lib.machine.cache_utils import (
    KEY_HOST_WIDE_RESIDUAL_HT,
    make_cache_key,
  )

  return make_cache_key(
    KEY_HOST_WIDE_RESIDUAL_HT,
    str(host_fingerprint),
    str(typ),
    str(day_iso),
    HOST_WIDE_RESIDUAL_CACHE_REV,
  )


def sorted_hosts_fingerprint(hosts: Any) -> str:
  """
  Stable fingerprint for a host list used in residual day-cache keys.

  Args:
    hosts (Any): Iterable of hostnames.

  Returns:
    str: Comma-joined sorted unique host strings (empty → ``-``).

  Examples:
    >>> sorted_hosts_fingerprint(["b", "a"])
    'a,b'
  """
  vals = sorted({str(h) for h in (hosts or []) if h})
  return ",".join(vals) if vals else "-"


def calendar_days_covering(start: Any, end: Any) -> list[str]:
  """
  Inclusive calendar-day ISO strings covering ``[start, end]``.

  Args:
    start (Any): Datetime-like window start.
    end (Any): Datetime-like window end.

  Returns:
    list[str]: ``YYYY-MM-DD`` days (empty when bounds missing).

  Examples:
    >>> from datetime import datetime, timezone
    >>> calendar_days_covering(
    ...   datetime(2026, 10, 21, 23, tzinfo=timezone.utc),
    ...   datetime(2026, 10, 22, 1, tzinfo=timezone.utc),
    ... )
    ['2026-10-21', '2026-10-22']
  """
  if start is None or end is None:
    return []
  try:
    from datetime import timedelta

    d0 = start.date() if hasattr(start, "date") else start
    d1 = end.date() if hasattr(end, "date") else end
    if d1 < d0:
      d0, d1 = d1, d0
    out: list[str] = []
    cur = d0
    while cur <= d1:
      out.append(cur.isoformat())
      cur = cur + timedelta(days=1)
    return out
  except Exception:
    return []


# Per-sample ceilings. Below a uint64 wrap (~1e19) and above a single host.
MAX_SANE_COUNTER_RATE = 1.0e13
MAX_SANE_COUNTER_DELTA = 1.0e17


def sane_counter_abs_limit(value_key: str) -> float:
  """
  Absolute ceiling for one ``host_data`` sample before aggregation.

  Args:
    value_key (str): ``arc``, ``value``, or ``delta``.

  Returns:
    float: Rate ceiling, or the larger delta ceiling for ``delta``.

  Examples:
    >>> sane_counter_abs_limit("delta") > sane_counter_abs_limit("arc")
    True
  """
  if value_key == "delta":
    return MAX_SANE_COUNTER_DELTA
  return MAX_SANE_COUNTER_RATE


def collapse_residual_sample_copies(
  rows: list[dict[str, Any]],
  value_key: str = "value",
) -> list[dict[str, Any]]:
  """
  One host series per (host, time, event).

  Hardware ``dev`` values are summed. ``j:`` copies are used only when no
  hardware dev is present, and then as a max so co-tenant stamps are not
  added. Samples at or above the sane ceiling are dropped.

  Args:
    rows (list[dict[str, Any]]): Rows with ``host``, ``time``, ``dev``,
      optional ``event``, and ``value_key``.
    value_key (str): Numeric field to reduce.

  Returns:
    list[dict[str, Any]]: Collapsed rows.

  Examples:
    >>> collapse_residual_sample_copies([])
    []
  """
  groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
  for row in rows:
    key = (row.get("host"), row.get("time"), row.get("event", ""))
    groups.setdefault(key, []).append(row)
  limit = sane_counter_abs_limit(value_key)
  out: list[dict[str, Any]] = []
  for key, items in groups.items():
    hardware = [
      row for row in items if not str(row.get("dev") or "").startswith("j:")
    ]
    chosen = hardware or [
      row for row in items if str(row.get("dev") or "").startswith("j:")
    ]
    vals: list[float] = []
    for row in chosen:
      raw = row.get(value_key)
      if raw is None:
        continue
      try:
        val = float(raw)
      except TypeError, ValueError:
        continue
      if val != val or abs(val) >= limit:
        continue
      vals.append(val)
    if not vals:
      continue
    reduced = sum(vals) if hardware else max(vals)
    out.append(
      {
        "host": key[0],
        "time": key[1],
        "event": key[2],
        value_key: reduced,
      }
    )
  return out


def apply_gpu_affinity_dev_filter(
  qs: Any,
  jid: str,
  *,
  host: str | None = None,
  affinity_devs: list[str] | None = None,
) -> Any:
  """
  When affinity GPU lists exist, restrict ``dev__in`` to those devices.

  Pass ``affinity_devs`` to avoid repeated DB lookups (``None`` means
  look up; empty list means no GPUs allowed; non-empty restricts).

  Args:
    qs (Any): Django queryset already scoped to GPU types.
    jid (str): Job id.
    host (str | None): Optional host for affinity lookup.
    affinity_devs (list[str] | None): Precomputed ``gpu_devs_for_job``
      result; ``None`` triggers a lookup.

  Returns:
    Any: Filtered queryset, or ``qs`` unchanged when unrestricted.

  Examples:
    >>> apply_gpu_affinity_dev_filter(None, "1")  # doctest: +SKIP
  """
  devs = affinity_devs
  if devs is None:
    devs = gpu_devs_for_job(jid, host=host)
  if devs is None:
    return qs
  return qs.filter(dev__in=list(devs))


def job_is_shared_node_from_jobish(jobish: Any) -> bool:
  """
  Resolve ``is_shared_node`` from a job-like object with a ``jid`` attribute.

  Args:
    jobish (Any): Object or mapping with ``jid`` (``jid_table``, ``job_data``).

  Returns:
    bool: Shared-node signal when jid is a real string/int; else False.

  Examples:
    >>> job_is_shared_node_from_jobish(type("J", (), {"jid": ""})())
    False
  """
  jid = getattr(jobish, "jid", None)
  if jid is None and isinstance(jobish, dict):
    jid = jobish.get("jid")
  if not isinstance(jid, (str, int)):
    return False
  s = str(jid).strip()
  if not s:
    return False
  return job_is_shared_node(s)


def job_is_shared_node(jid: str) -> bool:
  """
  True when the job has ``job_affinity`` rows (affinity-only).

  Used for Job Detail ``is_shared_node`` and shared-job help footers.
  Does not probe ``host_data`` for ``dev=j:<jid>`` (that EXISTS timed out
  under portal load and flooded statement-timeout ERROR logs). Missing
  affinity → False (omit shared footer).

  Args:
    jid (str): Job id.

  Returns:
    bool: True when affinity rows exist for ``jid``; else False.

  Examples:
    >>> job_is_shared_node("")  # doctest: +SKIP
    False
  """
  jid_s = str(jid or "").strip()
  if not jid_s:
    return False
  from hpcperfstats.site.lib.machine.models import job_affinity

  return job_affinity.objects.filter(jid=jid_s).exists()
