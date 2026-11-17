"""
Job Detail no-data reasons built from monitor value sentinels.

``compute_metrics`` explains a metric without a value from the
``host_sentinel_summary`` runs that overlap the job window: one part per
sentinel code, ``"<type>: <catalog description> (<n>/<N> hosts)"``, joined by
``"; "`` and capped at the ``metrics_data.no_data_reason`` column width. A
type the job's monitor schema never carried gets the schema-absence text
instead. Without sentinel evidence the caller keeps its generic reason, so
data from monitors that never emit sentinels reads exactly as before.

The machine-readable twin of each reason is the ``no_data_codes`` JSON
(``{"codes": {"-2": n_hosts}, "schema_absent": True, ...}``); the Job Detail
audience split reads it and never parses the text.

Attributes:
  REASON_MAX_CHARS (int): ``metrics_data.no_data_reason`` column width.
  REASON_ELLIPSIS (str): Suffix that marks a truncated reason.
  REASON_JOIN (str): Separator between the parts of one reason.
  SENTINEL_RUN_HOST_BATCH (int): Hosts per ``host_sentinel_summary`` read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, NamedTuple

from hpcperfstats.lib.monitor_sentinel import RESEARCHER_VISIBLE_CODES, lookup

REASON_MAX_CHARS = 512
REASON_ELLIPSIS = "…"
REASON_JOIN = "; "
SENTINEL_RUN_HOST_BATCH = 16


class SentinelRun(NamedTuple):
  """
  One ``host_sentinel_summary`` run, times as epoch microseconds.

  Attributes:
    host (str): Sample host name.
    type (str): Monitor type name.
    dev (str): Collapsed device (``''`` for host-summed types).
    event (str): Event name.
    code (int): Negative sentinel code.
    start_us (int): First sample of the run.
    end_us (int): Last sample of the run.
    device_count (int): Devices carrying ``code`` in each sample.
    total_devices (int): Device rows of the series in each sample.

  Examples:
    >>> SentinelRun("h1", "host_ib", "", "port_rcv_data", -2, 0, 10, 1, 1).full
    True
  """

  host: str
  type: str
  dev: str
  event: str
  code: int
  start_us: int
  end_us: int
  device_count: int
  total_devices: int

  @property
  def full(self) -> bool:
    """
    True when every device row of the series carried the code, so the
    collapsed ``host_data`` row itself is a sentinel (no value that sample).

    Returns:
      bool: ``device_count >= total_devices``.

    Examples:
      >>> SentinelRun("h", "t", "", "e", -3, 0, 1, 3, 64).full
      False
    """
    return self.device_count >= self.total_devices


def _epoch_us(value: datetime) -> int:
  """
  Epoch microseconds of an aware or naive (UTC) datetime.

  Args:
    value (datetime): Timestamp from the ORM.

  Returns:
    int: Epoch microseconds.

  Examples:
    >>> _epoch_us(datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC))
    1000000
  """
  if value.tzinfo is None:
    value = value.replace(tzinfo=UTC)
  return round(value.timestamp() * 1_000_000)


def load_sentinel_runs(
  hosts: Sequence[str], start_time: datetime, end_time: datetime
) -> list[SentinelRun]:
  """
  Read the ``host_sentinel_summary`` runs of ``hosts`` that overlap the
  window, in host batches over the ``(host, start_time, end_time)`` index.

  The side table is small (it grows with sentinel transitions, not time), so
  one read per host batch covers the whole window without time slicing.

  Args:
    hosts (Sequence[str]): Job host names (``host_data`` FQDNs).
    start_time (datetime): Window start.
    end_time (datetime): Window end.

  Returns:
    list[SentinelRun]: Overlapping runs, possibly overlapping each other
    (archive and live ingest can race); readers union them per series.

  Examples:
    >>> load_sentinel_runs([], None, None)
    []
  """
  if not hosts or start_time is None or end_time is None:
    return []
  from hpcperfstats.site.lib.machine.models import host_sentinel_summary

  out: list[SentinelRun] = []
  host_list = list(hosts)
  for i in range(0, len(host_list), SENTINEL_RUN_HOST_BATCH):
    qs = host_sentinel_summary.objects.filter(
      host__in=host_list[i : i + SENTINEL_RUN_HOST_BATCH],
      start_time__lte=end_time,
      end_time__gte=start_time,
    ).values_list(
      "host",
      "type",
      "dev",
      "event",
      "code",
      "start_time",
      "end_time",
      "device_count",
      "total_devices",
    )
    for host, typ, dev, event, code, t0, t1, n_dev, n_tot in qs:
      out.append(
        SentinelRun(
          str(host),
          str(typ),
          str(dev or ""),
          str(event),
          int(code),
          _epoch_us(t0),
          _epoch_us(t1),
          int(n_dev),
          int(n_tot),
        )
      )
  return out


def runs_for(
  runs: Iterable[SentinelRun],
  types: Iterable[str],
  events: Iterable[str] | None,
) -> list[SentinelRun]:
  """
  Runs of the given types, limited to ``events`` when any are named.

  Args:
    runs (Iterable[SentinelRun]): Job runs.
    types (Iterable[str]): Concrete type names (probe-expanded).
    events (Iterable[str] | None): Event names; empty or None keeps every
      event of the types.

  Returns:
    list[SentinelRun]: Matching runs.

  Examples:
    >>> r = SentinelRun("h", "host_ib", "", "a", -2, 0, 1, 1, 1)
    >>> (
    ...   len(runs_for([r], ["host_ib"], ["b"])),
    ...   len(runs_for([r], ["host_ib"], None)),
    ... )
    (0, 1)
  """
  type_set = set(types)
  event_set = set(events or ())
  return [
    r
    for r in runs
    if r.type in type_set and (not event_set or r.event in event_set)
  ]


def sentinel_host_histogram(
  runs: Iterable[SentinelRun],
) -> dict[int, set[str]]:
  """
  Hosts per sentinel code over runs where the whole series was unreadable.

  Partial-device runs (some devices still valid) do not make a host's value
  missing, so they are left to the partial-device scaling notes.

  Args:
    runs (Iterable[SentinelRun]): Runs of one metric's types and events.

  Returns:
    dict[int, set[str]]: ``{code: hosts}``.

  Examples:
    >>> rs = [
    ...   SentinelRun("h1", "t", "", "e", -2, 0, 1, 1, 1),
    ...   SentinelRun("h2", "t", "", "e", -3, 0, 1, 1, 64),
    ... ]
    >>> sentinel_host_histogram(rs)
    {-2: {'h1'}}
  """
  out: dict[int, set[str]] = {}
  for r in runs:
    if r.full:
      out.setdefault(r.code, set()).add(r.host)
  return out


def cap_reason(text: str) -> str:
  """
  Fit a reason into ``REASON_MAX_CHARS``, ending in ``REASON_ELLIPSIS`` when
  it had to be cut.

  Args:
    text (str): Full reason.

  Returns:
    str: ``text`` or its truncated form.

  Examples:
    >>> cap_reason("short")
    'short'
    >>> len(cap_reason("x" * 600))
    512
  """
  if len(text) <= REASON_MAX_CHARS:
    return text
  return text[: REASON_MAX_CHARS - len(REASON_ELLIPSIS)] + REASON_ELLIPSIS


def format_histogram_reason(
  typename: str, histogram: Mapping[int, set[str]], n_hosts: int
) -> str:
  """
  ``"<type>: <description> (<n>/<N> hosts)"`` per code, most hosts first,
  joined by ``REASON_JOIN`` and capped by :func:`cap_reason`.

  Args:
    typename (str): Monitor type named in the reason.
    histogram (Mapping[int, set[str]]): ``{code: hosts}``.
    n_hosts (int): Hosts in the job (the denominator); never below the
      hosts the histogram names.

  Returns:
    str: Reason text.

  Examples:
    >>> format_histogram_reason("host_ib", {-2: {"a", "b"}}, 4)
    'host_ib: counter not present (2/4 hosts)'
  """
  total = max(int(n_hosts), len(set().union(*histogram.values())))
  parts = [
    f"{typename}: {lookup(code).description} ({len(hosts)}/{total} hosts)"
    for code, hosts in sorted(
      histogram.items(), key=lambda kv: (-len(kv[1]), -kv[0])
    )
  ]
  return cap_reason(REASON_JOIN.join(parts))


def schema_absence_reason(typename: str) -> str:
  """
  Reason for a metric whose type the job's monitor schema never carried.

  Args:
    typename (str): Monitor type name.

  Returns:
    str: ``"<type> collector not found on this node's monitor"``.

  Examples:
    >>> schema_absence_reason("host_ib")
    "host_ib collector not found on this node's monitor"
  """
  return f"{typename} collector not found on this node's monitor"


def histogram_codes_json(
  histogram: Mapping[int, set[str]],
) -> dict[str, dict[str, int]]:
  """
  ``no_data_codes`` for a histogram reason: ``{"codes": {"-2": n_hosts}}``.

  Args:
    histogram (Mapping[int, set[str]]): ``{code: hosts}``.

  Returns:
    dict[str, dict[str, int]]: JSON-ready codes map.

  Examples:
    >>> histogram_codes_json({-2: {"a"}, -3: {"a", "b"}})
    {'codes': {'-2': 1, '-3': 2}}
  """
  return {
    "codes": {
      str(code): len(hosts)
      for code, hosts in sorted(histogram.items(), reverse=True)
    }
  }


def no_data_reason(
  *,
  schema: Mapping[str, Any] | None,
  runs: Iterable[SentinelRun],
  types: Sequence[str],
  events: Sequence[str] | None,
  n_hosts: int,
  legacy_reason: str,
  check_schema: bool,
  frame_histogram: Mapping[int, set[str]] | None = None,
) -> tuple[str, dict[str, Any] | None]:
  """
  Reason and ``no_data_codes`` for a metric that produced no value.

  Precedence: schema absence (when ``check_schema`` and a schema is known
  and none of ``types`` is in it), then the sentinel host histogram over
  ``types`` / ``events``, then ``legacy_reason`` with no codes.

  Args:
    schema (Mapping[str, Any] | None): ``jt.schema`` (type -> events).
    runs (Iterable[SentinelRun]): Job runs.
    types (Sequence[str]): Probe-expanded type names; the first one names
      the type in the text.
    events (Sequence[str] | None): Probe-expanded events, or None / empty
      for every event of the types.
    n_hosts (int): Hosts in the job.
    legacy_reason (str): Generic reason kept without sentinel evidence.
    check_schema (bool): Whether this metric reads only ``types``, so their
      absence from the schema means the collector is missing.
    frame_histogram (Mapping[int, set[str]] | None): ``{code: hosts}`` the
      metric fold excluded as static (``host_data`` sentinel rows), merged
      with the side-table histogram.

  Returns:
    tuple[str, dict[str, Any] | None]: ``(reason, no_data_codes)``.

  Examples:
    >>> no_data_reason(
    ...   schema={"host_cpu": ["user"]},
    ...   runs=[],
    ...   types=["host_ib"],
    ...   events=None,
    ...   n_hosts=1,
    ...   legacy_reason="legacy",
    ...   check_schema=True,
    ... )[1]
    {'codes': {}, 'schema_absent': True}
  """
  if check_schema and schema and not any(t in schema for t in types):
    return schema_absence_reason(types[0]), {"codes": {}, "schema_absent": True}
  histogram = sentinel_host_histogram(runs_for(runs, types, events))
  for code, hosts in (frame_histogram or {}).items():
    histogram.setdefault(code, set()).update(hosts)
  if not histogram:
    return legacy_reason, None
  return (
    format_histogram_reason(types[0], histogram, n_hosts),
    histogram_codes_json(histogram),
  )


def _pct(part: float, whole: float) -> int:
  """
  Whole-number percentage of ``part`` in ``whole`` (0 when ``whole`` is 0).

  Args:
    part (float): Numerator.
    whole (float): Denominator.

  Returns:
    int: Rounded percent.

  Examples:
    >>> _pct(1, 3)
    33
  """
  return 0 if not whole else round(100.0 * part / whole)


def _code_list(counts: Mapping[int, int], *, with_counts: bool) -> str:
  """
  Catalog descriptions of codes, most frequent first, comma separated.

  Args:
    counts (Mapping[int, int]): ``{code: count}``.
    with_counts (bool): Append each count.

  Returns:
    str: Text such as ``"read failed 2, counter not present 1"``.

  Examples:
    >>> _code_list({-3: 2, -2: 1}, with_counts=True)
    'read failed 2, counter not present 1'
  """
  items = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0]))
  return ", ".join(
    f"{lookup(c).description} {n}" if with_counts else lookup(c).description
    for c, n in items
  )


def quality_note(records: Sequence[Any]) -> str | None:
  """
  Staff quality note for a metric value the sentinel rules touched.

  Parts (joined by ``REASON_JOIN``): ``coverage N% (transient: <codes>)``,
  ``excluded hosts n/N (<codes>)``, ``estimated: u/N devices unreadable
  (<codes>), scaled``, ``estimated (filled N% of time)``, ``estimated:
  n/N hosts, scaled`` and ``bridged N samples``.

  Args:
    records (Sequence[Any]): ``sentinel_profile.FoldQuality`` records of
      the metric's folds.

  Returns:
    str | None: Capped note, or None when nothing applies.

  Examples:
    >>> quality_note([])
  """
  parts: list[str] = []
  for q in records:
    if q.samples and q.covered < q.samples:
      share = ", ".join(
        f"{lookup(c).description} {_pct(n, q.samples)}%"
        for c, n in sorted(
          q.transient_codes.items(), key=lambda kv: (-kv[1], -kv[0])
        )
      )
      parts.append(
        f"coverage {_pct(q.covered, q.samples)}% (transient: {share})"
      )
    if q.excluded:
      by_code: dict[int, int] = {}
      for code in q.excluded.values():
        by_code[code] = by_code.get(code, 0) + 1
      parts.append(
        f"excluded hosts {len(q.excluded)}/{q.n_hosts} "
        f"({_code_list(by_code, with_counts=False)})"
      )
    if q.partial_devices:
      devices = {c: v[0] for c, v in q.partial_devices.items()}
      total = max(v[1] for v in q.partial_devices.values())
      parts.append(
        f"estimated: {sum(devices.values())}/{total} devices unreadable "
        f"({_code_list(devices, with_counts=len(devices) > 1)}), scaled"
      )
    if q.filled:
      parts.append(f"estimated (filled {_pct(q.filled, q.buckets)}% of time)")
    if q.hosts_scaled:
      kept = q.n_hosts - len(q.excluded)
      parts.append(f"estimated: {kept}/{q.n_hosts} hosts, scaled")
    if q.bridged:
      parts.append(f"bridged {q.bridged} samples")
  return cap_reason(REASON_JOIN.join(parts)) if parts else None


def quality_codes(records: Sequence[Any]) -> dict[str, Any]:
  """
  ``no_data_codes`` for a metric value the sentinel rules touched.

  Shape: ``{"codes": {code: excluded hosts}, "transient": {code: samples},
  "partial_devices": {code: {"devices": u, "total": N}}, "bridged": n,
  "filled_frac": f, "excluded_hosts": n, "hosts_scaled": bool}``, summed
  over the records.

  Args:
    records (Sequence[Any]): ``sentinel_profile.FoldQuality`` records.

  Returns:
    dict[str, Any]: JSON-ready codes.

  Examples:
    >>> quality_codes([])["bridged"]
    0
  """
  codes: dict[str, int] = {}
  transient: dict[str, int] = {}
  partial: dict[str, dict[str, int]] = {}
  filled = buckets = bridged = excluded = 0
  scaled = False
  for q in records:
    for code in q.excluded.values():
      codes[str(code)] = codes.get(str(code), 0) + 1
    for code, n in q.transient_codes.items():
      transient[str(code)] = transient.get(str(code), 0) + n
    for code, (n_dev, n_tot) in q.partial_devices.items():
      cur = partial.setdefault(str(code), {"devices": 0, "total": 0})
      cur["devices"] = max(cur["devices"], n_dev)
      cur["total"] = max(cur["total"], n_tot)
    filled += q.filled
    buckets += q.buckets
    bridged += q.bridged
    excluded += len(q.excluded)
    scaled = scaled or q.hosts_scaled
  return {
    "codes": codes,
    "transient": transient,
    "partial_devices": partial,
    "bridged": bridged,
    "filled_frac": round(filled / buckets, 4) if buckets else 0.0,
    "excluded_hosts": excluded,
    "hosts_scaled": scaled,
  }


def researcher_visible(no_data_codes: Mapping[str, Any] | None) -> bool:
  """
  Whether a non-staff viewer may see the reason or note behind these codes.

  True for schema absence, or when every contributing code (excluded hosts,
  transient samples, partial devices) is in ``RESEARCHER_VISIBLE_CODES`` and
  nothing was estimated (bridged, filled or host-scaled). Rows without codes
  (generic reasons) are staff-only.

  Args:
    no_data_codes (Mapping[str, Any] | None): ``metrics_data.no_data_codes``.

  Returns:
    bool: Show the text to researchers.

  Examples:
    >>> researcher_visible({"codes": {"-2": 1}})
    True
    >>> researcher_visible({"codes": {"-2": 1, "-7": 1}})
    False
    >>> researcher_visible(None)
    False
  """
  if not isinstance(no_data_codes, Mapping):
    return False
  if no_data_codes.get("schema_absent"):
    return True
  contributing = {
    int(c)
    for key in ("codes", "transient", "partial_devices")
    for c in (no_data_codes.get(key) or {})
  }
  estimated = (
    no_data_codes.get("bridged")
    or no_data_codes.get("filled_frac")
    or no_data_codes.get("hosts_scaled")
  )
  return (
    bool(contributing)
    and not estimated
    and contributing <= RESEARCHER_VISIBLE_CODES
  )
