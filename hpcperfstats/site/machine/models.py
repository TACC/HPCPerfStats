"""The database models of hpcperfstats: job_data, metrics_data, host_data, proc_data, and RealField. Maps to TimescaleDB/PostgreSQL tables.

"""
from django.contrib.postgres.fields import ArrayField
from django.db import models


class RealField(models.FloatField):
  """Django field that uses PostgreSQL real (32-bit float) instead of double precision.

    """

  # Make type in order to use 32 bit floats (reals) instead of 64 bit floats
  def db_type(self, connection):
    """Return PostgreSQL type name 'real'.

        """
    return "real"


# manage.py inspectdb


class job_data(models.Model):
  """Slurm job accounting record: jid, times, runtime, user, account, queue, state, host_list, etc. Table: job_data.

    """
  jid = models.CharField(primary_key=True, max_length=32)
  submit_time = models.DateTimeField()
  start_time = models.DateTimeField()
  end_time = models.DateTimeField(db_index=True)
  runtime = models.FloatField(blank=True, null=True)
  timelimit = models.FloatField(blank=True, null=True)
  node_hrs = models.FloatField(blank=True, null=True)
  nhosts = models.IntegerField(blank=True, null=True)
  ncores = models.IntegerField(blank=True, null=True)
  username = models.CharField(max_length=64)
  account = models.CharField(max_length=64, blank=True, null=True)
  queue = models.CharField(max_length=64, blank=True, null=True)
  state = models.CharField(max_length=64, blank=True, null=True)
  QOS = models.CharField(max_length=64, blank=True, null=True)
  jobname = models.TextField(blank=True, null=True)
  host_list = ArrayField(models.TextField())

  class Meta:
    db_table = 'job_data'
    managed = True

  def __str__(self):
    """Return string representation (jid)."""
    return str(self.jid)

  def color(self):
    """Return hex color for state: E1EDFA completed, FFB2B2 failed, silver otherwise.

        """
    if self.state == 'COMPLETED':
      ret_val = "E1EDFA"
    elif self.state == 'FAILED':
      ret_val = "FFB2B2"
    else:
      ret_val = "silver"
    return ret_val


class metrics_data(models.Model):
  """Derived metric value per job and (type, metric). Unique on (jid, type, metric). Table: metrics_data.

    """
  jid = models.ForeignKey(job_data,
                          on_delete=models.CASCADE,
                          db_column='jid',
                          blank=True,
                          null=True)
  type = models.CharField(max_length=32, blank=True, null=True)
  metric = models.CharField(max_length=32, blank=True, null=True)
  units = models.CharField(max_length=16, blank=True, null=True)
  value = models.FloatField(blank=True, null=True)

  class Meta:
    managed = True
    db_table = 'metrics_data'
    unique_together = (('jid', 'type', 'metric'),)

  def __str__(self):
    """Return string representation jid_type_metric."""
    return str(self.jid_id or "") + "_" + str(self.type or "") + "_" + str(
        self.metric or "")


#Old Table SQL
"""
    query_create_hostdata_table = CREATE TABLE IF NOT EXISTS host_data (
                                               time  TIMESTAMPTZ NOT NULL,
                                               host  VARCHAR(64),
                                               jid   VARCHAR(32),
                                               type  VARCHAR(32),
                                               dev   VARCHAR(64),
                                               event VARCHAR(64),
                                               unit  VARCHAR(16),
                                               value real,
                                               delta real,
                                               arc   real,
                                               UNIQUE (time, host, type, event)
                                               );

                                          CREATE INDEX ON host_data (host, time DESC);
                                          CREATE INDEX ON host_data (jid, time DESC);

    SELECT create_hypertable('host_data', by_range('time', 86400000000));
    query_create_compression = ALTER TABLE host_data SET \
                                  (timescaledb.compress, timescaledb.compress_orderby = 'time DESC', timescaledb.compress_segmentby = 'host,jid,type,event');
                                  SELECT add_compression_policy('host_data', INTERVAL '12h', if_not_exists => true);


    query_create_process_table = CREATE TABLE IF NOT EXISTS proc_data (
    jid         VARCHAR(32) NOT NULL,
    host        VARCHAR(64),
    proc        VARCHAR(512),
    UNIQUE(jid, host, proc)
    );

    query_create_process_index = "CREATE INDEX ON proc_data (jid);"
"""


class host_data(models.Model):
  """TimescaleDB hypertable: per (time, host, jid, type, event) value/delta/arc. Table: host_data.

    """
  time = models.DateTimeField(primary_key=True)
  host = models.CharField(max_length=64, blank=True, null=True)
  jid = models.CharField(max_length=32, blank=True, null=True)
  type = models.CharField(max_length=32, blank=True, null=True)
  dev = models.CharField(max_length=64, blank=True, null=True)
  event = models.CharField(max_length=64, blank=True, null=True)
  unit = models.CharField(max_length=16, blank=True, null=True)
  value = RealField(null=True)
  arc = RealField(null=True)
  delta = RealField(null=True)

  class Meta:
    db_table = 'host_data'
    unique_together = (('time', 'host', 'type', 'event'),)
    indexes = [
        models.Index(fields=["host", "time"]),
        models.Index(fields=["jid", "time"]),
    ]


class proc_data(models.Model):
  """Process names observed per (jid, host). Table: proc_data.

    """
  jid = models.CharField(max_length=32, blank=True, null=True)
  host = models.CharField(max_length=64, blank=True, null=True)
  proc = models.CharField(max_length=512, blank=True, null=True)

  class Meta:
    managed = True
    db_table = 'proc_data'
    unique_together = (('jid', 'host', 'proc'),)
    indexes = [
        models.Index(fields=["jid"]),
    ]

  def __str__(self):
    """Return string representation (jid, host, proc)."""
    return f"{self.jid}:{self.host}:{self.proc}"
