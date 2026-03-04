import os
os.environ['OPENBLAS_NUM_THREADS'] = '4'

import warnings
warnings.simplefilter(action='ignore', category=UserWarning)
warnings.simplefilter(action='ignore', category=FutureWarning)

from bokeh.models import CustomJSTickFormatter
import numpy as np
from pandas import read_sql as rsql

import hpcperfstats.conf_parser as cfg


local_timezone = cfg.get_timezone()

class utils():
  def __init__(self, job):
    freq_list = {"intel_snb" : 2.7, "intel_ivb" : 2.8, "intel_hsw" : 2.3,
                 "intel_bdw" : 2.6, "intel_knl" : 1.4, "intel_skx" : 2.1,
                 "intel_8pmc3" : 2.7, "intel_4pmc3" : 2.7}
    imc_list  = ["intel_snb_imc", "intel_ivb_imc", "intel_hsw_imc",
                 "intel_bdw_imc", "intel_knl_mc_dclk", "intel_skx_imc"]
    cha_list = ["intel_knl_cha", "intel_skx_cha"]
    self.job = job
    self.nhosts = len(job.hosts.keys())
    self.hostnames  = sorted(job.hosts.keys())
    self.wayness = int(job.acct['cores'])/int(job.acct['nodes'])
    self.hours = ((job.times[:] - job.times[0])/3600.).astype(float)
    self.t = job.times
    self.nt = len(job.times)
    self.dt = (job.times[-1] - job.times[0]).astype(float)
    self.pmc = None
    self.imc = None
    self.cha = None
    self.freq = None
    for typename in  job.schemas.keys():
      if typename in freq_list:
          self.pmc  = typename
          self.freq = freq_list[typename]
      if typename in imc_list:
          self.imc = typename
      if typename in cha_list:
          self.cha = typename
  
  def get_type(self, typename, aggregate = True):

    if typename == "imc": typename = self.imc
    if typename == "pmc": typename = self.pmc
    if typename == "cha": typename = self.cha
    if not typename or typename is None: return None, {}

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

def read_sql(*args, **kwargs):

    df = rsql(*args, **kwargs)

    #df = clean_dataframe(df)
    return df

def clean_dataframe(df):
    df = df.fillna('')
    df = df.replace([np.inf, -np.inf], '')
    return df


def tz_aware_bokeh_tick_formatter():
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