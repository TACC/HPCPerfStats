"""Shared utilities for analysis: job-like utils class (freq/imc/cha, get_type), queryset_to_dataframe, clean_dataframe, and timezone-aware Bokeh tick formatter.

"""
from django.conf import settings
import hpcperfstats.conf_parser as cfg

openblas_threads = getattr(settings, "OPENBLAS_NUM_THREADS", 4)

import os

os.environ['OPENBLAS_NUM_THREADS'] = str(openblas_threads)

import warnings

warnings.simplefilter(action='ignore', category=UserWarning)
warnings.simplefilter(action='ignore', category=FutureWarning)

from bokeh.models import CustomJSTickFormatter
import numpy as np
import pandas as pd

local_timezone = cfg.get_timezone()


class utils():
  """Minimal job-like wrapper exposing host stats, schemas, times, and type resolution (pmc/imc/cha) for metrics and plots.

    """

  def __init__(self, job):
    """Initialize from a job object; set nhosts, hostnames, wayness, hours, t, nt, dt, and resolve pmc/imc/cha/freq from schemas.

        """
    freq_list = {
        "intel_snb": 2.7,
        "intel_ivb": 2.8,
        "intel_hsw": 2.3,
        "intel_bdw": 2.6,
        "intel_knl": 1.4,
        "intel_skx": 2.1,
        "intel_8pmc3": 2.7,
        "intel_4pmc3": 2.7
    }
    imc_list = [
        "intel_snb_imc", "intel_ivb_imc", "intel_hsw_imc", "intel_bdw_imc",
        "intel_knl_mc_dclk", "intel_skx_imc"
    ]
    cha_list = ["intel_knl_cha", "intel_skx_cha"]
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
    for typename in job.schemas.keys():
      if typename in freq_list:
        self.pmc = typename
        self.freq = freq_list[typename]
      if typename in imc_list:
        self.imc = typename
      if typename in cha_list:
        self.cha = typename

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
      if aggregate:
        stats[hostname] = 0
        for devname in host.stats[typename]:
          stats[hostname] += host.stats[typename][devname].astype(float)
      else:
        stats[hostname] = {}
        for devname in host.stats[typename]:
          stats[hostname][devname] = host.stats[typename][devname].astype(float)
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
    TIMEOUT_SHORT,
  )
  from hpcperfstats.site.machine.models import job_data
  from hpcperfstats.analysis.gen.jid_table import jid_table

  def _job_dict_fn():
    job_row = job_data.objects.filter(jid=jid).values().first()
    return dict(job_row) if job_row is not None else None

  job_dict = cached_orm(f"{KEY_JOB_DICT}:{jid}", TIMEOUT_SHORT, _job_dict_fn)
  if job_dict is None:
    return pd.DataFrame(), None

  jt = jid_table(jid)
  if jt.start_time is None or jt.end_time is None:
    return pd.DataFrame(), job_dict

  host_df = jt.get_full_host_data_df()
  return host_df, job_dict


def queryset_to_dataframe(qs):
  """Convert a Django QuerySet to a pandas DataFrame.

    """
  import pandas as pd
  if qs is None or not hasattr(qs, "values"):
    return pd.DataFrame()
  return pd.DataFrame(list(qs.values()))


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
