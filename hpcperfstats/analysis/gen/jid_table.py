"""Job-scoped host_data access via Django ORM. Provides jid_table, TypeDetailDataProvider, and HostDataProvider for querying job/host metrics without raw SQL. Uses Redis caching for heavy queries.

"""
import time
from datetime import timezone as dt_utc
import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.gen.utils import queryset_to_dataframe
from hpcperfstats.print_utils import log_print
from hpcperfstats.site.machine.cache_utils import (
    KEY_AGG_DF,
    KEY_JOB,
    KEY_JOB_HOST_LIST,
    KEY_JOB_SCHEMA,
    KEY_HOST_DATA_DF,
    KEY_HOST_SCHEMA,
    KEY_HOST_TIME_DF,
    KEY_LLITE_DELTA,
    KEY_TYPE_DETAIL_AGG,
    KEY_TYPE_DETAIL_HOST_TIME,
    cached_orm,
    make_cache_key,
    TIMEOUT_SHORT,
)
from hpcperfstats.site.machine.models import host_data, job_data
from django.db.models import Sum

local_timezone = cfg.get_local_timezone()


def _ensure_tz(dt):
  """Ensure datetime is timezone-aware in local_timezone for display.

    """
  if dt is None:
    return None
  if dt.tzinfo is None:
    from django.utils import timezone as django_tz
    dt = django_tz.make_aware(dt, dt_utc.utc)
  return dt.astimezone(local_timezone)


class jid_table:
  """Job-scoped view of job_data and host_data using Django ORM. No raw connection or temp tables; all data via ORM.

    """

  def __init__(self, jid):
    """Build job-scoped filter from job_data and populate host_list and schema from host_data.

        """
    log_print("Initializing table for job {0}".format(jid))

    self.jid = jid

    try:
      job = cached_orm(
          make_cache_key(KEY_JOB, jid),
          TIMEOUT_SHORT,
          lambda: job_data.objects.filter(jid=jid).only("host_list", "start_time", "end_time").first(),
      )
    except Exception:
      job = None

    if job is None:
      self.acct_host_list = []
      self.host_list = []
      self.schema = {}
      self.start_time = None
      self.end_time = None
      self._base_filter = {}
      return

    # job_data host_list: use fqdn for host_data lookups (cast to str for varchar comparison)
    self.acct_host_list = [
        str(h) + "." + cfg.get_host_name_ext() for h in (job.host_list or [])
    ]
    self.start_time = _ensure_tz(job.start_time)
    self.end_time = _ensure_tz(job.end_time)
    self._base_filter = {
        "time__gte": self.start_time,
        "time__lte": self.end_time,
        "host__in": self.acct_host_list,
    }

    # Distinct hosts that actually have host_data in range (cached)
    qtime = time.time()

    def _host_list_fn():
      host_qs = (host_data.objects.filter(**self._base_filter).values_list(
          "host", flat=True).distinct())
      return list(set(host_qs))

    _st = self.start_time.isoformat() if self.start_time else ""
    _et = self.end_time.isoformat() if self.end_time else ""
    self.host_list = cached_orm(
        make_cache_key(KEY_JOB_HOST_LIST, jid, _st, _et),
        TIMEOUT_SHORT,
        _host_list_fn,
    ) or []
    log_print("query time: {0:.1f}".format(time.time() - qtime))

    if len(self.host_list) == 0:
      self.schema = {}
      return

    # Schema: distinct (type, event) for one host (cached)
    etime = time.time()

    def _schema_fn():
      """Return distinct (type, event) pairs for one host as a DataFrame, using Django ORM only.

      Uses values_list(...).distinct() to avoid the Django bug that can raise
      IndexError in values().distinct() when schema and model definitions diverge.
      """
      import pandas as pd

      if not self.host_list:
        return pd.DataFrame(columns=["type", "event"])

      raw_rows = list(
          host_data.objects.filter(
              host=str(self.host_list[0]),
              time__gte=self._base_filter["time__gte"],
              time__lte=self._base_filter["time__lte"],
          )
          .values_list("type", "event")
          .distinct()
      )
      # Defensive: some backends/composite PKs can return extra columns or even
      # shorter tuples if the DB schema and model fields diverge. Keep only rows
      # with at least two elements and trim to the first two.
      rows = [tuple(r[:2]) for r in raw_rows if len(r) >= 2]
      if not rows:
        return pd.DataFrame(columns=["type", "event"])
      return pd.DataFrame(rows, columns=["type", "event"])

    schema_df = cached_orm(
        make_cache_key(KEY_JOB_SCHEMA, jid, self.host_list[0]),
        TIMEOUT_SHORT,
        _schema_fn,
    )
    if schema_df is None or schema_df.empty or "type" not in schema_df.columns:
      self.schema = {}
    else:
      types = sorted(schema_df["type"].unique().tolist())
      self.schema = {}
      for t in types:
        self.schema[t] = sorted(
            schema_df[schema_df["type"] == t]["event"].unique().tolist())
    log_print("schema time: {0:.1f}".format(time.time() - etime))

  def _host_data_qs(self, **extra_filters):
    """Base host_data queryset for this job (time range + hosts).

        """
    return host_data.objects.filter(**self._base_filter, **extra_filters)

  def get_host_time_df(self):
    """DataFrame of (host, time) distinct, ordered by host, time (cached).

        """
    def _fn():
      qs = (self._host_data_qs().values("host", "time").distinct().order_by(
          "host", "time"))
      return queryset_to_dataframe(qs)

    key = make_cache_key(KEY_HOST_TIME_DF, self.jid)
    result = cached_orm(key, TIMEOUT_SHORT, _fn)
    return result if result is not None else queryset_to_dataframe(None)

  def get_aggregate_df(self, typ, val_col, events, conv=1.0):
    """Aggregate val_col (e.g. 'arc' or 'value') for given type and events. Returns DataFrame with columns host, time, sum_val (sum * conv). Result is cached per (jid, typ, val_col, events).
        """
    events_key = ":".join(sorted(events))

    def _fn():
      hosts = [str(h) for h in self._base_filter.get("host__in") or []]
      if not hosts:
        import pandas as pd
        return pd.DataFrame(columns=["host", "time", "sum_val"])

      # Fetch raw samples (no SQL GROUP BY) and aggregate in pandas. This avoids
      # backend-specific grouping quirks that have been causing
      # "column host_data.host must appear in the GROUP BY" errors.
      qs = host_data.objects.filter(
          host__in=hosts,
          time__gte=self._base_filter["time__gte"],
          time__lte=self._base_filter["time__lte"],
          type=typ,
          event__in=list(events),
      ).values("host", "time", val_col)

      import pandas as pd

      df_raw = queryset_to_dataframe(qs)
      if df_raw.empty or "host" not in df_raw.columns or "time" not in df_raw.columns:
        return pd.DataFrame(columns=["host", "time", "sum_val"])

      df_grouped = (
          df_raw.groupby(["host", "time"], as_index=False)[val_col].sum()
          .rename(columns={val_col: "sum_val"})
          .sort_values(["host", "time"])
      )
      df_grouped["sum_val"] = df_grouped["sum_val"] * conv
      return df_grouped

    key = make_cache_key(KEY_AGG_DF, self.jid, typ, val_col, events_key)
    result = cached_orm(key, TIMEOUT_SHORT, _fn)
    if result is not None:
      return result
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  def get_full_host_data_df(self, columns=None):
    """Full host_data for this job as DataFrame (host, time, type, event, value, etc.). Cached when columns is None.

        """
    cols = columns or ["host", "time", "type", "event", "value", "arc", "delta"]

    # When specific columns are requested, return a fresh DataFrame without
    # caching to mirror previous behaviour (no cache on the raw-SQL path).
    # Use values_list defensively and filter/trim tuples to avoid Django's
    # "tuple index out of range" bug when model fields and DB schema diverge.
    if columns is not None:
      import pandas as pd

      qs = (
          self._host_data_qs()
          .values_list(*cols)
          .order_by("host", "time")
      )
      rows = []
      for r in qs:
        # Some backends or mismatched schemas can return scalar values or
        # shorter tuples. Keep only rows that are sequences with at least
        # len(cols) elements.
        if not isinstance(r, (list, tuple)):
          continue
        if len(r) < len(cols):
          continue
        rows.append(tuple(r[:len(cols)]))
      return pd.DataFrame(rows, columns=cols)

    def _fn():
      import pandas as pd

      qs = (
          self._host_data_qs()
          .values_list(*cols)
          .order_by("host", "time")
      )
      rows = []
      for r in qs:
        if not isinstance(r, (list, tuple)):
          continue
        if len(r) < len(cols):
          continue
        rows.append(tuple(r[:len(cols)]))
      return pd.DataFrame(rows, columns=cols)

    key = make_cache_key(KEY_HOST_DATA_DF, self.jid)
    result = cached_orm(key, TIMEOUT_SHORT, _fn)
    return result if result is not None else queryset_to_dataframe(None)

  def get_llite_delta_by_event(self):
    """Lustre read_bytes/write_bytes sum(delta) by event for this job (cached).

        """
    from django.db.models import Sum

    def _llite_fn():
      qs = (self._host_data_qs(
          type="llite",
          event__in=["read_bytes", "write_bytes"],
      ).values("event").annotate(delta_sum=Sum("delta")).order_by("event"))
      return queryset_to_dataframe(qs)

    key = make_cache_key(KEY_LLITE_DELTA, self.jid)
    result = cached_orm(key, TIMEOUT_SHORT, _llite_fn)
    return result if result is not None else queryset_to_dataframe(None)

  def close(self):
    """No-op; provided for context-manager symmetry.

        """
    pass

  def __enter__(self):
    """Context manager entry; return self."""
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    """Context manager exit; call close()."""
    self.close()
    return False

  def __del__(self):
    """Destructor; call close() if possible. Prefer using 'with jid_table(...)'
    for guaranteed cleanup; __del__ is not guaranteed to run (e.g. at interpreter
    shutdown or with circular refs)."""
    try:
      self.close()
    except Exception:
      pass


class TypeDetailDataProvider:
  """ORM-based provider for type-detail view: host_data filtered by jid, type, time range. Used by DevPlot instead of raw connection + temp table type_detail.

    """

  def __init__(self, jid, type_name, start_time, end_time, host_list):
    """Build base filter for jid, type_name, time range, and optional host_list.

        """
    self.jid = jid
    self.type_name = type_name
    self.start_time = start_time
    self.end_time = end_time
    self.host_list = list(host_list) if host_list else []
    self._base_filter = {
        "jid": jid,
        "type": type_name,
        "time__gte": start_time,
        "time__lte": end_time,
    }
    if self.host_list:
      self._base_filter["host__in"] = self.host_list

  def _qs(self, **extra):
    """Base host_data queryset for this provider (jid, type, time range, optional host_list).

        """
    return host_data.objects.filter(**self._base_filter, **extra)

  def get_host_time_df(self):
    """DataFrame of (host, time) distinct, ordered by host, time (cached).

        """
    _st = self.start_time.isoformat() if self.start_time else ""
    _et = self.end_time.isoformat() if self.end_time else ""
    key = make_cache_key(
        KEY_TYPE_DETAIL_HOST_TIME, self.jid, self.type_name, _st, _et
    )

    def _fn():
      qs = (self._qs().values("host", "time").distinct().order_by("host", "time"))
      return queryset_to_dataframe(qs)

    result = cached_orm(key, TIMEOUT_SHORT, _fn)
    return result if result is not None else queryset_to_dataframe(None)

  def get_events_units(self):
    """List of (event, unit) for one host.

        """
    if not self.host_list:
      return []
    qs = (self._qs(host=self.host_list[0]).values("event", "unit").distinct())
    df = queryset_to_dataframe(qs)
    if df.empty:
      return []
    return list(df[["event", "unit"]].itertuples(index=False, name=None))

  def get_type_list(self):
    """Return sorted list of distinct type names for the first host.

        """
    if not self.host_list:
      return []
    qs = self._qs(host=self.host_list[0]).values_list("type",
                                                      flat=True).distinct()
    return sorted(set(qs))

  def get_aggregate_df(self, event, metric="arc"):
    """Aggregate metric (e.g. arc) by host and time for the given event; returns DataFrame with sum_val (cached)."""
    _ALLOWED_METRICS = ("arc", "value", "delta")
    if metric not in _ALLOWED_METRICS:
      metric = "arc"
    _st = self.start_time.isoformat() if self.start_time else ""
    _et = self.end_time.isoformat() if self.end_time else ""
    key = make_cache_key(
        KEY_TYPE_DETAIL_AGG, self.jid, self.type_name, event, metric, _st, _et
    )

    def _fn():
      # Avoid DB-level GROUP BY here because some deployments have reported
      # backend-specific grouping SQL errors for this query shape.
      qs = self._qs(event=event).values("host", "time", metric)
      import pandas as pd

      df_raw = queryset_to_dataframe(qs)
      if (
          df_raw.empty
          or "host" not in df_raw.columns
          or "time" not in df_raw.columns
          or metric not in df_raw.columns
      ):
        return pd.DataFrame(columns=["host", "time", "sum_val"])
      return (
          df_raw.groupby(["host", "time"], as_index=False)[metric]
          .sum()
          .rename(columns={metric: "sum_val"})
          .sort_values(["host", "time"])
      )

    result = cached_orm(key, TIMEOUT_SHORT, _fn)
    if result is not None:
      return result
    return pd.DataFrame()


class HostDataProvider:
  """ORM-based provider for host-scoped host_data (one host, time range). Same interface as jid_table for SummaryPlot: jid, host_list, get_host_time_df, get_aggregate_df.

    """

  def __init__(self, host_fqdn, start_time, end_time):
    """Build base filter and schema for one host and time range. Schema is cached.

        """
    self.jid = host_fqdn.split(".")[0].replace("-", "_")
    self.host_list = [host_fqdn]
    self._base_filter = {
        "host": host_fqdn,
        "time__gte": start_time,
        "time__lte": end_time,
    }
    # Schema: distinct (type, event) for this host (cached)
    _st = start_time.isoformat() if start_time else ""
    _et = end_time.isoformat() if end_time else ""
    cache_key = make_cache_key(KEY_HOST_SCHEMA, host_fqdn, _st, _et)

    def _schema_fn():
      """Return schema dict {type: [events...]} for this host/time range using ORM only."""
      import pandas as pd

      raw_rows = list(
          host_data.objects.filter(
              host=str(self._base_filter["host"]),
              time__gte=self._base_filter["time__gte"],
              time__lte=self._base_filter["time__lte"],
          )
          .values_list("type", "event")
          .distinct()
      )
      # Defensive: keep only rows with at least two elements and trim to first
      # two columns in case backend adds extras or returns shorter tuples.
      rows = [tuple(r[:2]) for r in raw_rows if len(r) >= 2]
      if not rows:
        return {}
      schema_df = pd.DataFrame(rows, columns=["type", "event"])
      if schema_df.empty:
        return {}
      types = sorted(schema_df["type"].unique().tolist())
      schema = {}
      for t in types:
        schema[t] = sorted(
            schema_df[schema_df["type"] == t]["event"].unique().tolist())
      return schema

    self.schema = cached_orm(cache_key, TIMEOUT_SHORT, _schema_fn) or {}

  def _host_data_qs(self, **extra_filters):
    """Base host_data queryset for this host (time range).

        """
    return host_data.objects.filter(**self._base_filter, **extra_filters)

  def get_host_time_df(self):
    """DataFrame of (host, time) distinct, ordered by host, time.

        """
    qs = (self._host_data_qs().values("host", "time").distinct().order_by(
        "host", "time"))
    return queryset_to_dataframe(qs)

  def get_aggregate_df(self, typ, val_col, events, conv=1.0):
    """Aggregate val_col for type and events; returns DataFrame with host, time, sum_val (sum * conv)."""
    _ALLOWED_METRICS = ("arc", "value", "delta")
    if val_col not in _ALLOWED_METRICS:
      val_col = "arc"
    from django.db.models import Sum
    # Aggregate by host and time using ORM; host filter is already in _base_filter.
    qs = (
        self._host_data_qs(
            type=typ,
            event__in=list(events),
        )
        .values("host", "time")
        .annotate(sum_val=Sum(val_col))
        .order_by("host", "time")
    )
    df = queryset_to_dataframe(qs)
    if not df.empty and "sum_val" in df.columns:
      df["sum_val"] = df["sum_val"] * conv
    return df
