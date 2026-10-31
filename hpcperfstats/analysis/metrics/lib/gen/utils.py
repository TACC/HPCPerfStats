"""
Shared utilities for analysis: job-like utils class (freq/imc/cha, get_type),.

queryset_to_dataframe, clean_dataframe, and timezone-aware Bokeh tick formatter.

Attributes:
  jid_table: ``jid_table``.
  job_data: ``job_data``.
  local_timezone: ``local_timezone``.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from bokeh.models import (
  BasicTickFormatter,
  DatetimeTickFormatter,
  LinearAxis,
)

import hpcperfstats.dbload.lib.conf_parser as cfg

warnings.simplefilter(action="ignore", category=UserWarning)
warnings.simplefilter(action="ignore", category=FutureWarning)

# Lazy-imported ORM dependencies so that this module can be imported in
# environments without a configured database and without creating circular
# imports with jid_table. Tests patch these names directly.
job_data = None
jid_table = None

local_timezone = cfg.get_timezone()

# Canonical monitor typenames (see hpcperfstats.dbload.lib.monitor_naming); dual-read via resolve.
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
  pmc_freq_for_typename,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  cha_typename_priority,
  imc_types_probe_order,
  pmc_typename_priority,
  type_probe_names,
)


def _coerce_schema_typename_key(key: Any) -> Any:
  """
  Make jid schema keys hashable/set-safe (never raw lists from bad payloads).

  Args:
    key (Any): Key passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _coerce_schema_typename_key(None)  # doctest: +SKIP
  """
  if isinstance(key, str):
    return key
  if isinstance(key, (list, tuple, set)):
    return ",".join(str(v) for v in key)
  if isinstance(key, dict):
    try:
      import json

      return json.dumps(key, sort_keys=True, separators=(",", ":"))
    except TypeError:
      return str(key)
  return str(key)


def _pick_pmc_typename(schema_keys: Any) -> Any:
  """
  First PMC typename present in schema_keys (canonical + legacy priority).

  Args:
    schema_keys (Any): Schema keys passed to this helper.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> _pick_pmc_typename(None)  # doctest: +SKIP
  """
  keys = {_coerce_schema_typename_key(k) for k in schema_keys}
  for typename in pmc_typename_priority():
    if typename in keys:
      return typename
  return None


class utils:
  """
  Minimal job-like wrapper exposing host stats, schemas, times, and type.

    resolution (pmc/imc/cha) for metrics and plots.

  Attributes:
    cha: ``cha``.
    dt: ``dt``.
    freq: ``freq``.
    hostnames: ``hostnames``.
    hours: ``hours``.
    imc: ``imc``.
    job: ``job``.
    nhosts: ``nhosts``.
    nt: ``nt``.
    pmc: ``pmc``.
    t: ``t``.
    wayness: ``wayness``.
  """

  def __init__(self, job: Any) -> None:
    """
    Initialize from a job object; set nhosts, hostnames, wayness, hours, t, nt,.

      dt, and resolve pmc/imc/cha/freq from schemas.

    Args:
      job (Any): Job record (Django ``job_data`` or job-like mapping).

    Returns:
      None

    Examples:
      >>> utils(None)  # doctest: +SKIP
    """
    imc_list = list(imc_types_probe_order())
    cha_list = list(cha_typename_priority())
    self.job = job
    self.nhosts = len(job.hosts.keys())
    self.hostnames = sorted(job.hosts.keys())
    self.wayness = int(job.acct["cores"]) / int(job.acct["nodes"])
    self.hours = ((job.times[:] - job.times[0]) / 3600.0).astype(float)
    self.t = job.times
    self.nt = len(job.times)
    self.dt = (job.times[-1] - job.times[0]).astype(float)
    self.pmc = None
    self.imc = None
    self.cha = None
    self.freq = None
    sk = job.schemas.keys()
    pmc_pick = _pick_pmc_typename(sk)
    if pmc_pick is not None:
      self.pmc = pmc_pick
      self.freq = pmc_freq_for_typename(pmc_pick)
    for imc_typ in imc_list:
      if imc_typ in sk:
        self.imc = imc_typ
        break
    for cha_typ in cha_list:
      if cha_typ in sk:
        self.cha = cha_typ
        break

  def get_type(self, typename: Any, aggregate: bool = True) -> Any:
    """
    Return (schema, stats) for typename (e.g. pmc/imc/cha); stats is per-host.

      aggregated or per-device dict. Returns (None, {}) if type not in job.

    Args:
      typename (Any): Typename passed to this helper.
      aggregate (bool): Boolean flag for aggregate.

    Returns:
      Any: Value produced by this call (type depends on inputs).

    Examples:
      >>> utils().get_type(None, True)  # doctest: +SKIP
    """
    if typename == "imc":
      typename = self.imc
    if typename == "pmc":
      typename = self.pmc
    if typename == "cha":
      typename = self.cha
    if not typename or typename is None:
      return None, {}

    resolved_typename = typename
    if resolved_typename not in self.job.schemas:
      for alt in type_probe_names(typename):
        if alt in self.job.schemas:
          resolved_typename = alt
          break
      else:
        return None, {}
    schema = self.job.schemas[resolved_typename]
    stats = {}
    for hostname, host in self.job.hosts.items():
      # Some hosts may not expose this stats type (e.g. GPU-less nodes when
      # aggregating "nvidia_gpu"). Skip those hosts instead of raising KeyError.
      host_type_stats = host.stats.get(resolved_typename)
      if host_type_stats is None:
        continue
      if aggregate:
        host_sum = 0
        for devname in host_type_stats:
          host_sum += host_type_stats[devname].astype(float)
        stats[hostname] = host_sum
      else:
        stats[hostname] = {}
        for devname in host_type_stats:
          stats[hostname][devname] = host_type_stats[devname].astype(float)
    return schema, stats


def iter_queryset_values_dicts(
  qs: Any,
  *fields: Any,
  chunk_size: int = 2000,
) -> Iterator[Any]:
  """
  Yield dict rows from ``QuerySet.values(*fields)`` without ``list(qs)``.

  Use for large querysets where callers process incrementally (see also
  ``jid_table`` large-job time sampling for job-scoped bounds).

  Args:
    qs (Any): Qs passed to this helper.
    *fields (Any): Extra positional values for ``fields``; element types match
    the helper's documented protocol.
    chunk_size (int): Integer value for chunk size.

  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).

  Examples:
    >>> iter_queryset_values_dicts(None, 0)  # doctest: +SKIP
  """
  if qs is None or not fields:
    return
  yield from qs.values(*fields).iterator(chunk_size=max(1, int(chunk_size)))


def non_degenerate_y_range_for_series(
  series: Any,
  y_range_end: Any | None = None,
) -> Any:
  """
  Return (y_min, y_max) with NaN-safe non-degenerate bounds.

  Args:
    series (Any): Array-like of numeric values (NaN allowed).
    y_range_end (Any | None): Explicit upper bound, or None for
      ``1.1 * max``.

  Returns:
    Any: ``(float, float)`` with ``y_max > y_min``.

  Examples:
    >>> non_degenerate_y_range_for_series([1.0, 3.0])
    (0.0, 3.3000000000000003)
    >>> non_degenerate_y_range_for_series([float("nan")])
    (0.0, 1.0)
  """
  values = np.asarray(series, dtype=np.float64).reshape(-1)
  finite = values[np.isfinite(values)]
  y_min_value = float(finite.min()) if finite.size else float("nan")
  if y_range_end is None or (
    isinstance(y_range_end, float) and math.isnan(y_range_end)
  ):
    y_range_end = 1.1 * float(finite.max()) if finite.size else float("nan")
  y_range_start = y_min_value if y_min_value < 0 else 0.0
  if math.isnan(float(y_range_end)):
    y_range_end = 0.0
  if math.isnan(y_range_start):
    y_range_start = 0.0
  if y_range_end <= y_range_start:
    y_range_end = y_range_start + 1
  return float(y_range_start), float(y_range_end)


def format_plain_decimal(value: Any, precision: int = 2) -> Any:
  """
  Format a numeric value without scientific notation for Bokeh hovers.

  Args:
    value (Any): Value to inspect (typically a numeric scalar).
    precision (int): Integer value for precision.

  Returns:
    Any: Value produced by this call (type depends on inputs).

  Examples:
    >>> format_plain_decimal(1234.5)
    '1,234.50'
  """
  if value is None or value == "":
    return ""
  try:
    number = float(value)
  except TypeError, ValueError:
    return str(value)
  if not math.isfinite(number):
    return str(value)
  return f"{number:,.{precision}f}"


def _as_local_datetime(value: Any) -> datetime | None:
  """
  Coerce a plot time to a cluster-local aware datetime.

  Naive datetimes and naive ``numpy.datetime64`` are cluster wall clock
  (see :func:`timestamps_as_cluster_naive`); aware datetimes and epoch
  milliseconds convert from UTC.

  Args:
    value (Any): Datetime, ``numpy.datetime64``, epoch ms number or None.

  Returns:
    datetime | None: Local aware datetime.

  Examples:
    >>> _as_local_datetime(None) is None
    True
  """
  tz = ZoneInfo(local_timezone)
  if value is None or value == "":
    return None
  if isinstance(value, np.datetime64):
    if np.isnat(value):
      return None
    micros = int(value.astype("datetime64[us]").astype(np.int64))
    naive = datetime(1970, 1, 1) + timedelta(microseconds=micros)
    return naive.replace(tzinfo=tz)
  if isinstance(value, (int, float, np.integer, np.floating)):
    if not math.isfinite(float(value)):
      return None
    return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC).astimezone(tz)
  if hasattr(value, "to_pydatetime"):
    value = value.to_pydatetime()
  if isinstance(value, datetime):
    return (
      value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)
    )
  return None


def format_cluster_hover_datetime(value: Any) -> Any:
  """
  Format a plot time in the configured cluster timezone (``H:MM AM``).

  Args:
    value (Any): Naive cluster-local datetime / ``datetime64``, aware
      datetime, or epoch milliseconds.

  Returns:
    Any: Formatted time, or ``""`` for None / empty.

  Examples:
    >>> format_cluster_hover_datetime(None)
    ''
  """
  local = _as_local_datetime(value)
  if local is None:
    return ""
  formatted = local.strftime("%I:%M %p")
  return formatted[1:] if formatted.startswith("0") else formatted


def timestamps_as_cluster_naive(times: Any) -> np.ndarray:
  """
  UTC sample times -> naive cluster wall clock (``datetime64[ms]``) for Bokeh.

  Bokeh's ``DatetimeTickFormatter`` has no timezone property and formats in
  UTC. Shifting to naive cluster local makes axis ticks match
  :func:`format_cluster_hover_datetime` without CustomJS.

  Args:
    times (Any): int64 epoch microseconds, ``datetime64`` array, or an
      iterable of aware datetimes.

  Returns:
    np.ndarray: ``datetime64[ms]`` naive local timestamps.

  Examples:
    >>> timestamps_as_cluster_naive(np.array([0], dtype=np.int64)).dtype
    dtype('<M8[ms]')
  """
  arr = np.asarray(times)
  if arr.dtype.kind == "M":
    micros = arr.astype("datetime64[us]").astype(np.int64)
  elif arr.dtype.kind in "iuf":
    micros = arr.astype(np.int64)
  else:
    micros = np.array(
      [
        round(
          (t if t.tzinfo else t.replace(tzinfo=UTC)).timestamp() * 1_000_000
        )
        for t in arr.tolist()
      ],
      dtype=np.int64,
    )
  tz = ZoneInfo(local_timezone)
  if micros.size == 0:
    return np.empty(0, dtype="datetime64[ms]")
  # One UTC offset per distinct second: DST-safe and cheap for dense axes.
  secs = micros // 1_000_000
  uniq, inverse = np.unique(secs, return_inverse=True)
  offsets = np.array(
    [
      int(
        datetime.fromtimestamp(int(s), tz=UTC)
        .astimezone(tz)
        .utcoffset()
        .total_seconds()
      )
      for s in uniq.tolist()
    ],
    dtype=np.int64,
  )
  local_us = micros + offsets[inverse] * 1_000_000
  return (local_us // 1000).astype("datetime64[ms]")


def hover_plain_columns(
  times: Any,
  numeric: dict[str, Any],
) -> dict[str, list[str]]:
  """
  Pre-formatted hover columns so HoverTool needs no CustomJS.

  Args:
    times (Any): Naive cluster-local ``datetime64`` array (or None).
    numeric (dict[str, Any]): Column name to numeric array.

  Returns:
    dict[str, list[str]]: ``_hover_time`` (when ``times`` is given) and one
    ``<name>_plain`` list per numeric column.

  Examples:
    >>> hover_plain_columns(None, {"x": [1.5]})
    {'x_plain': ['1.50']}
  """
  out: dict[str, list[str]] = {}
  if times is not None:
    out["_hover_time"] = [
      format_cluster_hover_datetime(t) for t in np.asarray(times).tolist()
    ]
  for name, values in numeric.items():
    out[f"{name}_plain"] = [
      format_plain_decimal(v)
      for v in np.asarray(values, dtype=np.float64).tolist()
    ]
  return out


def tz_aware_bokeh_tick_formatter() -> Any:
  """
  Datetime axis labels for cluster-naive plot times (no CustomJS / unsafe-eval).

  Callers must pass x values through ``timestamps_as_cluster_naive`` so tick
  strings match hover (cluster INI timezone).

  Returns:
    Any: ``DatetimeTickFormatter``.

  Examples:
    >>> tz_aware_bokeh_tick_formatter().hours
    '%I:%M %p'
  """
  return DatetimeTickFormatter(
    hours="%I:%M %p",
    minutes="%I:%M %p",
    hourmin="%I:%M %p",
    days="%m/%d",
    months="%b %Y",
  )


def new_plain_linear_tick_formatter() -> Any:
  """
  Bokeh tick labels without scientific notation (new instance per axis/plot).

  Returns:
    Any: ``BasicTickFormatter``.

  Examples:
    >>> new_plain_linear_tick_formatter().use_scientific
    False
  """
  return BasicTickFormatter(use_scientific=False, precision=2)


def new_plain_log_tick_formatter() -> Any:
  """
  Log-scale tick labels without scientific notation (built-in
  ``BasicTickFormatter``).

  Returns:
    Any: ``BasicTickFormatter``.

  Examples:
    >>> new_plain_log_tick_formatter().use_scientific
    False
  """
  return BasicTickFormatter(use_scientific=False, precision=2)


def set_linear_axes_plain_numeric(plot: Any) -> None:
  """
  Apply non-scientific tick formatters to every LinearAxis on the figure.

  Args:
    plot (Any): Bokeh figure.

  Returns:
    None

  Examples:
    >>> set_linear_axes_plain_numeric(None)  # doctest: +SKIP
  """
  for axis_list in (plot.xaxis, plot.yaxis):
    for ax in axis_list:
      if isinstance(ax, LinearAxis):
        ax.formatter = new_plain_linear_tick_formatter()
