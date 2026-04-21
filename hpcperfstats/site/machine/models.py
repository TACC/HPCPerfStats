"""The database models of hpcperfstats: job_data, metrics_data, host_data, proc_data, and RealField. Maps to TimescaleDB/PostgreSQL tables.

"""
from django.contrib.postgres.fields import ArrayField
import hashlib
import hmac
import secrets

from django.db import models
from django.db.models import Q


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
  # Sum over hosts of COUNT(DISTINCT time) in host_data for this job window
  # (jid-scoped live check by default; legacy uses accounting FQDNs from host_list).
  # NULL until first persist.
  metrics_distinct_time_count = models.IntegerField(blank=True, null=True)
  # Distinct host_data (type -> events) for the job window; written by update_metrics.
  # API uses this before falling back to live jid_table.schema queries.
  host_data_schema_json = models.JSONField(blank=True, null=True)

  class Meta:
    db_table = 'job_data'
    managed = True
    indexes = [
        models.Index(fields=["username"], name="job_data_username_idx"),
        models.Index(fields=["account"], name="job_data_account_idx"),
        models.Index(fields=["queue"], name="job_data_queue_idx"),
        models.Index(fields=["state"], name="job_data_state_idx"),
        models.Index(fields=["start_time"], name="job_data_start_time_idx"),
        models.Index(fields=["end_time", "username"], name="job_data_end_time_username_idx"),
        models.Index(fields=["queue", "end_time"], name="job_data_queue_end_time_idx"),
        models.Index(fields=["end_time", "state"], name="job_data_end_time_state_idx"),
    ]

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
  jid = models.ForeignKey(
      job_data,
      on_delete=models.CASCADE,
      db_column='jid',
      related_name='metrics_data_set',
      blank=True,
      null=True,
  )
  type = models.CharField(max_length=32, blank=True, null=True)
  metric = models.CharField(max_length=32, blank=True, null=True)
  units = models.CharField(max_length=16, blank=True, null=True)
  value = models.FloatField(blank=True, null=True)
  # When value is null after a metrics run, explains why (distinguishes
  # "computed, no data" from legacy incomplete rows that still need update_metrics).
  no_data_reason = models.CharField(max_length=512, blank=True, null=True)

  class Meta:
    managed = True
    db_table = 'metrics_data'
    unique_together = (('jid', 'type', 'metric'),)
    indexes = [
        models.Index(fields=["metric"], name="metrics_data_metric_idx"),
        models.Index(fields=["jid", "metric"], name="metrics_data_jid_metric_idx"),
        models.Index(
            fields=["jid"],
            name="metrics_data_stale_jid_idx",
            condition=Q(value__isnull=True)
            & (Q(no_data_reason__isnull=True) | Q(no_data_reason="")),
        ),
    ]

  def __str__(self):
    """Return string representation jid_type_metric."""
    return str(self.jid_id or "") + "_" + str(self.type or "") + "_" + str(
        self.metric or "")


#Old Table SQL
"""
    query_create_hostdata_table = CREATE TABLE IF NOT EXISTS host_data (
                                               time  TIMESTAMPTZ NOT NULL,
                                               host  VARCHAR(64),
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

    SELECT create_hypertable('host_data', by_range('time', 86400000000));
    query_create_compression = ALTER TABLE host_data SET \
                                  (timescaledb.compress, timescaledb.compress_orderby = 'time DESC', timescaledb.compress_segmentby = 'host,type,event');
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
  """TimescaleDB hypertable: per (time, host, jid, type, event) value/delta/arc.

  Job/sample scoping in the application uses job_data.start_time/end_time and
  job_data.host_list (FQDNs); host_data.jid is retained for compatibility and
  ad-hoc queries but is not used when gathering job samples.
  Table: host_data.

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
        models.Index(fields=["host", "-time"], name="host_data_host_time_desc_idx"),
        models.Index(fields=["jid", "-time"], name="host_data_jid_time_desc_idx"),
        models.Index(fields=["jid", "type", "event", "time"],
                     name="host_data_jid_type_ev_time_idx"),
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


class job_plot_artifact(models.Model):
  """Persisted Bokeh json_item payloads for job-level plots (gzip-compressed JSON).

  One row per (job, plot_kind, layout). Invalidated when host_data changes for the job.
  """

  jid = models.ForeignKey(
      job_data,
      on_delete=models.CASCADE,
      db_column="jid",
      related_name="plot_artifacts",
  )
  plot_kind = models.CharField(max_length=32)
  layout = models.CharField(max_length=16)
  payload_compressed = models.BinaryField()
  payload_encoding = models.CharField(max_length=32)
  input_fingerprint = models.CharField(max_length=64)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  class Meta:
    db_table = "job_plot_artifact"
    managed = True
    constraints = [
        models.UniqueConstraint(
            fields=["jid", "plot_kind", "layout"],
            name="job_plot_artifact_jid_kind_layout_uniq",
        ),
    ]

  def __str__(self):
    return f"{self.jid_id}:{self.plot_kind}:{self.layout}"


class job_detail_artifact(models.Model):
  """Persisted derived job detail/type-detail payloads (gzip-compressed JSON)."""

  jid = models.ForeignKey(
      job_data,
      on_delete=models.CASCADE,
      db_column="jid",
      related_name="detail_artifacts",
  )
  artifact_kind = models.CharField(max_length=32)
  artifact_scope = models.CharField(max_length=128, default="")
  payload_compressed = models.BinaryField()
  payload_encoding = models.CharField(max_length=32)
  input_fingerprint = models.CharField(max_length=64)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  class Meta:
    db_table = "job_detail_artifact"
    managed = True
    constraints = [
        models.UniqueConstraint(
            fields=["jid", "artifact_kind", "artifact_scope"],
            name="job_detail_artifact_jid_kind_scope_uniq",
        ),
    ]

  def __str__(self):
    return f"{self.jid_id}:{self.artifact_kind}:{self.artifact_scope}"


class ApiKey(models.Model):
  """API key for programmatic access, bound to an authenticated username.

  Keys are created via an OAuth-protected web page and then used by external
  tools (e.g. hpcperfstats-jobstats, hpcperfstats-sacct-gen) via the Authorization: Api-Key header.
  """

  key = models.CharField(max_length=64, primary_key=True)
  key_prefix = models.CharField(max_length=12, db_index=True, default="")
  username = models.CharField(max_length=128, db_index=True)
  created_at = models.DateTimeField(auto_now_add=True)
  last_used_at = models.DateTimeField(null=True, blank=True)
  is_active = models.BooleanField(default=True)
  is_staff = models.BooleanField(default=False)

  class Meta:
    db_table = "api_keys"
    managed = True
    indexes = [
        models.Index(fields=["username"], name="api_keys_username_idx"),
    ]

  def __str__(self):
    """Return short representation prefix@username."""
    shown = self.key_prefix or self.key[:8]
    return f"{shown}... for {self.username}"

  @staticmethod
  def hash_raw_key(raw_key: str) -> str:
    """Return stable SHA-256 hash for persisted API key lookup."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

  @staticmethod
  def make_raw_key() -> str:
    """Generate a new API key value shown once to the user."""
    return secrets.token_hex(32)

  @classmethod
  def create_from_raw_key(cls, username: str, is_staff: bool):
    """Create a key row from a generated raw key, returning (obj, raw_key)."""
    raw_key = cls.make_raw_key()
    key_hash = cls.hash_raw_key(raw_key)
    obj = cls.objects.create(
        key=key_hash,
        key_prefix=raw_key[:12],
        username=username,
        is_staff=is_staff,
    )
    return obj, raw_key

  def matches_raw_key(self, raw_key: str) -> bool:
    """Constant-time comparison helper for explicit validation paths."""
    return hmac.compare_digest(self.key, self.hash_raw_key(raw_key))
