"""
Shared utilities for analysis: job-like utils class (freq/imc/cha, get_type),.

queryset_to_dataframe, clean_dataframe, and timezone-aware Bokeh tick formatter.

Attributes:
  jid_table: ``jid_table``.
  job_data: ``job_data``.
  local_timezone: ``local_timezone``.
"""
from __future__ import annotations

from typing import Any, Iterator

import hpcperfstats.dbload.lib.conf_parser as cfg

import warnings

from bokeh.models import (
    BasicTickFormatter,
    DatetimeTickFormatter,
    LinearAxis,
)
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.simplefilter(action='ignore', category=UserWarning)
warnings.simplefilter(action='ignore', category=FutureWarning)

# Lazy-imported ORM dependencies so that this module can be imported in
# environments without a configured database and without creating circular
# imports with jid_table. Tests patch these names directly.
job_data = None
jid_table = None

local_timezone = cfg.get_timezone()

# Canonical monitor typenames (see hpcperfstats.dbload.lib.monitor_naming); dual-read via resolve.
from hpcperfstats.dbload.lib.monitor_naming.canonical import (  # noqa: E402
    pmc_freq_for_typename,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (  # noqa: E402
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


class utils():
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
    self.wayness = int(job.acct['cores']) / int(job.acct['nodes'])
    self.hours = ((job.times[:] - job.times[0]) / 3600.).astype(float)
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


def get_job_host_data_and_job_dict(jid: Any) -> Any:
  """
  Return (host_data_df, job_dict) for the given job id.
  
  host_data_df: DataFrame of all host_data rows within the job's start/end
  times and from only the hosts in the job (from job_data.host_list).
  job_dict: dictionary of the job_data row matching jid, or None if not found.
  Job row lookup is cached.
  
  Args:
    jid (Any): Jid passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_job_host_data_and_job_dict(None)  # doctest: +SKIP
  """
  from hpcperfstats.site.lib.machine.cache_utils import (
    KEY_JOB_DICT,
    cached_orm,
    get_site_content_cache_timeout,
    make_cache_key,
  )

  def _job_dict_fn() -> Any:
    """
    Internal helper to handle job dict function.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _job_dict_fn()  # doctest: +SKIP
    """
    row = job_data.objects.filter(jid=jid).values().first()
    return dict(row) if row is not None else None

  global job_data, jid_table
  if job_data is None:
    from hpcperfstats.site.lib.machine.models import job_data as _job_data
    job_data = _job_data
  if jid_table is None:
    from hpcperfstats.analysis.metrics.lib.gen.jid_table import jid_table as _jid_table
    jid_table = _jid_table

  try:
    job_dict = cached_orm(
        make_cache_key(KEY_JOB_DICT, jid),
        get_site_content_cache_timeout(),
        _job_dict_fn,
    )
  except Exception:
    job_dict = None
  if job_dict is None:
    return pd.DataFrame(), None

  jt = jid_table(jid)
  if jt.start_time is None or jt.end_time is None:
    return pd.DataFrame(), job_dict

  host_df = jt.get_full_host_data_df()
  return host_df, job_dict


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
  for row in qs.values(*fields).iterator(chunk_size=max(1, int(chunk_size))):
    yield row


def queryset_to_dataframe(qs: Any, columns: Any | None = None) -> Any:
  """
  Convert a Django QuerySet to a pandas DataFrame.
  
  When columns is set, uses qs.values(*columns). When columns is None,
  iterates the queryset as-is so annotated/grouped querysets are preserved.
  Handles iterable of dicts, list of lists/tuples, or model instances
  (via model_to_dict).
  
  Args:
    qs (Any): Qs passed to this helper.
    columns (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> queryset_to_dataframe(None, None)  # doctest: +SKIP
  """
  import pandas as pd
  if qs is None:
    return pd.DataFrame()
  if columns is not None and hasattr(qs, "values"):
    rows = list(qs.values(*columns))
    if not rows:
      return pd.DataFrame(columns=list(columns))
    return pd.DataFrame(rows)
  data = list(qs)
  if not data:
    # Empty .values() querysets must keep column names so callers can sort/concat
    # (e.g. jid_table.get_host_time_df); plain .none() has no values_select.
    q = getattr(qs, "query", None)
    vs = getattr(q, "values_select", None) if q is not None else None
    if vs:
      return pd.DataFrame(columns=list(vs))
    return pd.DataFrame()
  if isinstance(data[0], dict):
    return pd.DataFrame(data)
  if isinstance(data[0], (list, tuple)):
    return pd.DataFrame(data)
  from django.forms.models import model_to_dict
  return pd.DataFrame([model_to_dict(row) for row in data])


def non_degenerate_y_range_for_series(
  series: Any,
  y_range_end: Any | None = None,
) -> Any:
  """
  Return (y_min, y_max) with NaN-safe non-degenerate bounds.
  
  Args:
    series (Any): Series passed to this helper.
    y_range_end (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> non_degenerate_y_range_for_series(None, None)  # doctest: +SKIP
  """
  y_min_value = series.min()
  if y_range_end is None or pd.isna(y_range_end):
    y_range_end = 1.1 * series.max()
  y_range_start = y_min_value if y_min_value < 0 else 0
  if pd.isna(y_range_end):
    y_range_end = 0
  if pd.isna(y_range_start):
    y_range_start = 0
  if y_range_end <= y_range_start:
    y_range_end = y_range_start + 1
  return float(y_range_start), float(y_range_end)


def clean_dataframe(df: Any) -> Any:
  """
  Replace NaN and inf with empty string for display/serialization.
  
  Args:
    df (Any): Df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> clean_dataframe(None)  # doctest: +SKIP
  """
  df = df.fillna('')
  df = df.replace([np.inf, -np.inf], '')
  return df


def format_plain_decimal(value: Any, precision: int = 2) -> Any:
  """
  Format a numeric value without scientific notation for Bokeh hovers.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
    precision (int): Integer value for precision.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> format_plain_decimal(None, 0)  # doctest: +SKIP
  """
  if value is None or value == "":
    return ""
  try:
    number = float(value)
  except (TypeError, ValueError):
    return str(value)
  if not math.isfinite(number):
    return str(value)
  return f"{number:,.{precision}f}"


def format_cluster_hover_datetime(value: Any) -> Any:
  """
  Format Bokeh datetime or epoch-ms in the configured cluster timezone.
  
  Naive datetimes are treated as cluster wall clock (see
  ``timestamps_as_cluster_naive``); aware/epoch-ms values convert from UTC.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> format_cluster_hover_datetime(None)  # doctest: +SKIP
  """
  if value is None or value == "":
    return ""
  if isinstance(value, (int, float)) and math.isfinite(value):
    ts = pd.Timestamp(value, unit="ms", tz="UTC")
    local = ts.tz_convert(ZoneInfo(local_timezone))
  else:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
      # Cluster-naive plot series: already wall clock in ``local_timezone``.
      local = ts
    else:
      local = ts.tz_convert(ZoneInfo(local_timezone))
  formatted = local.strftime("%I:%M %p")
  if formatted.startswith("0"):
    formatted = formatted[1:]
  return formatted


def timestamps_as_cluster_naive(series: Any) -> Any:
  """
  UTC (or naive-as-UTC) timestamps → naive cluster wall clock for Bokeh axes.
  
  Bokeh 3.9 ``DatetimeTickFormatter`` has no timezone property and formats in
  UTC. Shifting to naive cluster local makes axis ticks match
  ``format_cluster_hover_datetime`` without CustomJS.
  
  Args:
    series (Any): Series passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> timestamps_as_cluster_naive(None)  # doctest: +SKIP
  """
  utc = pd.to_datetime(series, utc=True)
  local = utc.dt.tz_convert(ZoneInfo(local_timezone))
  return local.dt.tz_localize(None)


def add_hover_plain_columns(
  df: Any,
  numeric_cols: Any,
  time_col: str = "time",
) -> Any:
  """
  Add pre-formatted hover columns so HoverTool does not need CustomJS.
  
  Args:
    df (Any): Df passed to this helper.
    numeric_cols (Any): Numeric cols passed to this helper.
    time_col (str): String for time col.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> add_hover_plain_columns(None, None, "x")  # doctest: +SKIP
  """
  out = df.copy()
  if time_col in out.columns:
    out["_hover_time"] = out[time_col].map(format_cluster_hover_datetime)
  for col in numeric_cols:
    if col in out.columns:
      out[f"{col}_plain"] = out[col].map(format_plain_decimal)
  return out


def tz_aware_bokeh_tick_formatter() -> Any:
  """
  Datetime axis labels for cluster-naive plot times (no CustomJS / unsafe-eval).
  
  Callers must pass x values through ``timestamps_as_cluster_naive`` so tick
  strings match hover (cluster INI timezone).
  
  Returns:
    Any: Open return polymorphism from ``tz_aware_bokeh_tick_formatter``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> tz_aware_bokeh_tick_formatter()  # doctest: +SKIP
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
    Any: Open return polymorphism from ``new_plain_linear_tick_formatter``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> new_plain_linear_tick_formatter()  # doctest: +SKIP
  """
  return BasicTickFormatter(use_scientific=False, precision=2)


def new_plain_log_tick_formatter() -> Any:
  """
  Log-scale tick labels without scientific notation (built-in.
  
    BasicTickFormatter).
  
  Returns:
    Any: Open return polymorphism from ``new_plain_log_tick_formatter``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> new_plain_log_tick_formatter()  # doctest: +SKIP
  """
  return BasicTickFormatter(use_scientific=False, precision=2)


def set_linear_axes_plain_numeric(plot: Any) -> None:
  """
  Apply non-scientific tick formatters to every LinearAxis on the figure.
  
  Args:
    plot (Any): Plot passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> set_linear_axes_plain_numeric(None)  # doctest: +SKIP
  """
  for axis_list in (plot.xaxis, plot.yaxis):
    for ax in axis_list:
      if isinstance(ax, LinearAxis):
        ax.formatter = new_plain_linear_tick_formatter()
