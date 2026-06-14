"""Shared utilities for analysis: job-like utils class (freq/imc/cha, get_type), queryset_to_dataframe, clean_dataframe, and timezone-aware Bokeh tick formatter.

"""
import hpcperfstats.conf_parser as cfg

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

# Canonical monitor typenames (see hpcperfstats.monitor_naming); dual-read via resolve.
from hpcperfstats.monitor_naming.canonical import (  # noqa: E402
    ARM_IMC_STATS_TYPES,
    CHA_TYPENAME_PRIORITY,
    INTEL_CORE_PMC_TYPES_ORDERED,
    INTEL_FP_ARITH_ALL_EVENTS,
    INTEL_FP_ARITH_DOUBLE_EVENTS,
    INTEL_FP_ARITH_SINGLE_EVENTS,
    INTEL_IMC_STATS_TYPES,
    INTEL_LEGACY_SSE_FLOP_EVENTS,
    PMC_TYPENAME_PRIORITY,
    pmc_freq_for_typename,
)
from hpcperfstats.monitor_naming.resolve import (  # noqa: E402
    cha_typename_priority,
    imc_types_probe_order,
    pmc_typename_priority,
    type_probe_names,
)



def _coerce_schema_typename_key(key):
  """Make jid schema keys hashable/set-safe (never raw lists from bad payloads)."""
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


def _pick_pmc_typename(schema_keys):
  """First PMC typename present in schema_keys (canonical + legacy priority)."""
  keys = {_coerce_schema_typename_key(k) for k in schema_keys}
  for typename in pmc_typename_priority():
    if typename in keys:
      return typename
  return None


class utils():
  """Minimal job-like wrapper exposing host stats, schemas, times, and type resolution (pmc/imc/cha) for metrics and plots.

    """

  def __init__(self, job):
    """Initialize from a job object; set nhosts, hostnames, wayness, hours, t, nt, dt, and resolve pmc/imc/cha/freq from schemas.

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

  def get_type(self, typename, aggregate=True):
    """Return (schema, stats) for typename (e.g. pmc/imc/cha); stats is per-host aggregated or per-device dict. Returns (None, {}) if type not in job.

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


def get_job_host_data_and_job_dict(jid):
  """Return (host_data_df, job_dict) for the given job id.

  host_data_df: DataFrame of all host_data rows within the job's start/end
  times and from only the hosts in the job (from job_data.host_list).
  job_dict: dictionary of the job_data row matching jid, or None if not found.
  Job row lookup is cached.
  """
  from hpcperfstats.site.machine.cache_utils import (
    KEY_JOB_DICT,
    cached_orm,
    get_site_content_cache_timeout,
    make_cache_key,
  )

  def _job_dict_fn():
    row = job_data.objects.filter(jid=jid).values().first()
    return dict(row) if row is not None else None

  global job_data, jid_table
  if job_data is None:
    from hpcperfstats.site.machine.models import job_data as _job_data
    job_data = _job_data
  if jid_table is None:
    from hpcperfstats.analysis.gen.jid_table import jid_table as _jid_table
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


def iter_queryset_values_dicts(qs, *fields, chunk_size=2000):
  """Yield dict rows from ``QuerySet.values(*fields)`` without ``list(qs)``.

  Use for large querysets where callers process incrementally (see also
  ``jid_table`` large-job time sampling for job-scoped bounds).
  """
  if qs is None or not fields:
    return
  for row in qs.values(*fields).iterator(chunk_size=max(1, int(chunk_size))):
    yield row


def queryset_to_dataframe(qs, columns=None):
  """Convert a Django QuerySet to a pandas DataFrame.

  When columns is set, uses qs.values(*columns). When columns is None,
  iterates the queryset as-is so annotated/grouped querysets are preserved.
  Handles iterable of dicts, list of lists/tuples, or model instances
  (via model_to_dict).
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


def non_degenerate_y_range_for_series(series, y_range_end=None):
  """Return (y_min, y_max) with NaN-safe non-degenerate bounds."""
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


def clean_dataframe(df):
  """Replace NaN and inf with empty string for display/serialization.

    """
  df = df.fillna('')
  df = df.replace([np.inf, -np.inf], '')
  return df


def format_plain_decimal(value, precision=2):
  """Format a numeric value without scientific notation for Bokeh hovers."""
  if value is None or value == "":
    return ""
  try:
    number = float(value)
  except (TypeError, ValueError):
    return str(value)
  if not math.isfinite(number):
    return str(value)
  return f"{number:,.{precision}f}"


def format_cluster_hover_datetime(value):
  """Format Bokeh datetime or epoch-ms in the configured cluster timezone."""
  if value is None or value == "":
    return ""
  if isinstance(value, (int, float)) and math.isfinite(value):
    ts = pd.Timestamp(value, unit="ms", tz="UTC")
  else:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
      ts = ts.tz_localize("UTC")
  local = ts.tz_convert(ZoneInfo(local_timezone))
  formatted = local.strftime("%I:%M %p")
  if formatted.startswith("0"):
    formatted = formatted[1:]
  return formatted


def add_hover_plain_columns(df, numeric_cols, time_col="time"):
  """Add pre-formatted hover columns so HoverTool does not need CustomJS."""
  out = df.copy()
  if time_col in out.columns:
    out["_hover_time"] = out[time_col].map(format_cluster_hover_datetime)
  for col in numeric_cols:
    if col in out.columns:
      out[f"{col}_plain"] = out[col].map(format_plain_decimal)
  return out


def tz_aware_bokeh_tick_formatter():
  """Datetime axis labels via Bokeh built-in formatter (no CustomJS / unsafe-eval)."""
  return DatetimeTickFormatter(
      hours="%I:%M %p",
      minutes="%I:%M %p",
      hourmin="%I:%M %p",
      days="%m/%d",
      months="%b %Y",
  )


def new_plain_linear_tick_formatter():
  """Bokeh tick labels without scientific notation (new instance per axis/plot)."""
  return BasicTickFormatter(use_scientific=False, precision=2)


def new_plain_log_tick_formatter():
  """Log-scale tick labels without scientific notation (built-in BasicTickFormatter)."""
  return BasicTickFormatter(use_scientific=False, precision=2)


def set_linear_axes_plain_numeric(plot):
  """Apply non-scientific tick formatters to every LinearAxis on the figure."""
  for axis_list in (plot.xaxis, plot.yaxis):
    for ax in axis_list:
      if isinstance(ax, LinearAxis):
        ax.formatter = new_plain_linear_tick_formatter()
