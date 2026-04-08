"""Shared utilities for analysis: job-like utils class (freq/imc/cha, get_type), queryset_to_dataframe, clean_dataframe, and timezone-aware Bokeh tick formatter.

"""
import hpcperfstats.conf_parser as cfg

import warnings

from bokeh.models import (
    BasicTickFormatter,
    CustomJSHover,
    CustomJSTickFormatter,
    LinearAxis,
)
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

# Intel IMC types that expose CAS_READS/CAS_WRITES (used by roofline and utils.imc).
# For Knights Landing, the monitor stats type is "intel_knl_mc"; the dbload
# hardware_counter_maps/intel_process layer normalizes per-function names to
# "intel_knl_mc_dclk" (DRAM clock domain). INTEL_IMC_STATS_TYPES uses the
# normalized typename so metric/roofline code can treat KNL like other IMC
# generations.
INTEL_IMC_STATS_TYPES = (
    "intel_snb_imc",
    "intel_ivb_imc",
    "intel_hsw_imc",
    "intel_bdw_imc",
    "intel_knl_mc_dclk",
    "intel_skx_imc",
)

# ARM memory-controller types that expose CAS_READS/CAS_WRITES semantics.
ARM_IMC_STATS_TYPES = ("arm_imc",)

# FP_ARITH events for Intel/LIKWID core counters (roofline, summary first-win tries).
INTEL_FP_ARITH_DOUBLE_EVENTS = (
    "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE",
    "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE",
    "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE",
    "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE",
)
INTEL_FP_ARITH_SINGLE_EVENTS = (
    "FP_ARITH_INST_RETIRED_SCALAR_SINGLE",
    "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE",
    "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE",
    "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE",
)
INTEL_FP_ARITH_ALL_EVENTS = INTEL_FP_ARITH_DOUBLE_EVENTS + INTEL_FP_ARITH_SINGLE_EVENTS

# Intel SSE/AVX double FLOP proxies when FP_ARITH is absent (metrics, roofline, vecpercent).
INTEL_LEGACY_SSE_FLOP_EVENTS = (
    ("SSE_DOUBLE_SCALAR", 1),
    ("SSE_DOUBLE_PACKED", 2),
    ("SIMD_DOUBLE_256", 4),
)

# Intel core PMC typenames tried in order (summary/roofline); LIKWID last when both exist.
INTEL_CORE_PMC_TYPES_ORDERED = (
    "intel_8pmc3",
    "intel_4pmc3",
    "cpu_counter_metrics",
)

# Nominal GHz for APERF/MPERF ratio in avg_freq when typename matches.
_PMC_FREQ_BY_TYPENAME = {
    "intel_snb": 2.7,
    "intel_ivb": 2.8,
    "intel_hsw": 2.3,
    "intel_bdw": 2.6,
    "intel_knl": 1.4,
    "intel_skx": 2.1,
    "intel_8pmc3": 2.7,
    "intel_4pmc3": 2.7,
    "amd64_pmc": 2.7,
    "cpu_counter_metrics": 2.7,
}

# Prefer explicit order when multiple PMC-capable types appear in one job schema.
PMC_TYPENAME_PRIORITY = (
    "amd64_pmc",
    "intel_8pmc3",
    "intel_4pmc3",
    "cpu_counter_metrics",
    "intel_skx",
    "intel_knl",
    "intel_bdw",
    "intel_hsw",
    "intel_ivb",
    "intel_snb",
)

CHA_TYPENAME_PRIORITY = ("intel_skx_cha", "intel_knl_cha")


def _pick_pmc_typename(schema_keys):
  """First PMC typename present in schema_keys using PMC_TYPENAME_PRIORITY, else any known key."""
  keys = set(schema_keys)
  for typename in PMC_TYPENAME_PRIORITY:
    if typename in keys:
      return typename
  for typename in keys:
    if typename in _PMC_FREQ_BY_TYPENAME:
      return typename
  return None


class utils():
  """Minimal job-like wrapper exposing host stats, schemas, times, and type resolution (pmc/imc/cha) for metrics and plots.

    """

  def __init__(self, job):
    """Initialize from a job object; set nhosts, hostnames, wayness, hours, t, nt, dt, and resolve pmc/imc/cha/freq from schemas.

        """
    imc_list = list(INTEL_IMC_STATS_TYPES)
    cha_list = list(CHA_TYPENAME_PRIORITY)
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
      self.freq = _PMC_FREQ_BY_TYPENAME.get(pmc_pick, 2.7)
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

    if typename not in self.job.schemas:
      return None, {}
    schema = self.job.schemas[typename]
    stats = {}
    for hostname, host in self.job.hosts.items():
      # Some hosts may not expose this stats type (e.g. GPU-less nodes when
      # aggregating "nvidia_gpu"). Skip those hosts instead of raising KeyError.
      host_type_stats = host.stats.get(typename)
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


def tz_aware_bokeh_tick_formatter():
  """Return a fresh CustomJSTickFormatter that renders datetime ticks in the configured timezone. Must return a new instance per plot/document.

    """
  # Must return a fresh model per plot/document (Bokeh models cannot be shared
  # across documents, e.g. across separate web requests).
  return CustomJSTickFormatter(
      args={"tz": local_timezone},
      code="""
// Bokeh datetimes are milliseconds since epoch. Render tick labels in tz.
const dt = new Date(tick)

function pad2(n) { return (n < 10) ? ("0" + n) : ("" + n) }

try {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  }).formatToParts(dt)

  const out = {}
  for (const p of parts) out[p.type] = p.value
  return `${out.hour}:${out.minute} ${out.dayPeriod}`
} catch (e) {
  // Fallback: UTC without Intl timezone support or invalid tz name.
  return `${pad2(dt.getUTCHours())}:${pad2(dt.getUTCMinutes())}`
}
""",
  )


# JavaScript: decimal (non-scientific) strings for Bokeh hovers and log-axis ticks.
_PLAIN_NUMBER_HOVER_JS = """
const v = value;
if (v == null || v === "") return "";
const n = typeof v === "number" ? v : Number(v);
if (Number.isFinite(n))
  return new Intl.NumberFormat("en-US", {notation: "standard", minimumFractionDigits: 2, maximumFractionDigits: 2}).format(n);
return String(v);
"""

_PLAIN_LOG_TICK_JS = """
const t = tick;
if (t == null || t === "") return "";
const n = typeof t === "number" ? t : Number(t);
if (!Number.isFinite(n)) return String(t);
return new Intl.NumberFormat("en-US", {notation: "standard", minimumFractionDigits: 2, maximumFractionDigits: 2}).format(n);
"""


def new_plain_linear_tick_formatter():
  """Bokeh tick labels without scientific notation (new instance per axis/plot)."""
  return BasicTickFormatter(use_scientific=False, precision=2)


def new_plain_log_tick_formatter():
  """Log-scale tick labels as plain decimals (avoids 10^n style from default log formatter)."""
  return CustomJSTickFormatter(code=_PLAIN_LOG_TICK_JS.strip())


def new_plain_number_hover_formatter():
  """Hover tooltip numeric fields without scientific notation (new instance per HoverTool)."""
  return CustomJSHover(code=_PLAIN_NUMBER_HOVER_JS.strip())


def set_linear_axes_plain_numeric(plot):
  """Apply non-scientific tick formatters to every LinearAxis on the figure."""
  for axis_list in (plot.xaxis, plot.yaxis):
    for ax in axis_list:
      if isinstance(ax, LinearAxis):
        ax.formatter = new_plain_linear_tick_formatter()
