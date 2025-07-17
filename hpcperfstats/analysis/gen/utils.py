import os
os.environ['OPENBLAS_NUM_THREADS'] = '4'
import numpy as np
import sqlalchemy

import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

from pandas import read_sql as rsql

import hpcperfstats.conf_parser as cfg

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
    if not typename: return

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

def read_sql(sql, conn, **kwargs):
    # Pandas changed to only accepting sqlalchemy objects, so we will replace the old conn with sqlalchemy
    sa_conn = sqlalchemy.create_engine(cfg.get_db_sqlalchemy_connection_string())
    df = rsql(sql, sa_conn, **kwargs)

    #df = clean_dataframe(df)
    return df

def clean_dataframe(df):
    df = df.fillna('')
    df = df.replace([np.inf, -np.inf], '')
    return df
