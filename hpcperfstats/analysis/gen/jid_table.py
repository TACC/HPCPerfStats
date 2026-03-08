"""Job-scoped host_data access via Django ORM. Provides jid_table, TypeDetailDataProvider, and HostDataProvider for querying job/host metrics without raw SQL. Uses memcached caching for heavy queries.

"""
import time
from datetime import timezone as dt_utc
from zoneinfo import ZoneInfo

import hpcperfstats.conf_parser as cfg
from hpcperfstats.site.machine.cache_utils import (
    KEY_JOB,
    KEY_JOB_HOST_LIST,
    KEY_JOB_SCHEMA,
    KEY_LLITE_DELTA,
    cached_orm,
    TIMEOUT_SHORT,
)
from hpcperfstats.site.machine.models import host_data, job_data

_tz_cfg = cfg.get_timezone()
local_timezone = ZoneInfo(_tz_cfg) if isinstance(_tz_cfg, str) else _tz_cfg


def _ensure_tz(dt):
  """Ensure datetime is timezone-aware in local_timezone for display.

    """
  if dt is None:
    return None
  if dt.tzinfo is None:
    from django.utils import timezone as django_tz
    dt = django_tz.make_aware(dt, dt_utc.utc)
  return dt.astimezone(local_timezone)


def _queryset_to_dataframe(qs, columns=None):
  """Convert a Django QuerySet (values() or values_list()) to a pandas DataFrame.

    """
  import pandas as pd
  if qs is None:
    return pd.DataFrame()
  if hasattr(qs, "values") and columns is None:
    vals = qs.values()
    if vals is not None:
      return pd.DataFrame(list(vals))
  if hasattr(qs, "values") and columns is not None:
    vals = qs.values(*columns)
    if vals is not None:
      return pd.DataFrame(list(vals))
  return pd.DataFrame(list(qs))


class jid_table:
  """Job-scoped view of job_data and host_data using Django ORM. No raw connection or temp tables; all data via ORM.

    """

  def __init__(self, jid):
    """Build job-scoped filter from job_data and populate host_list and schema from host_data.

        """
    print("Initializing table for job {0}".format(jid))

    self.jid = jid
    self.conj = None  # Deprecated: no raw connection; kept for API compatibility.

    try:
      job = cached_orm(
          f"{KEY_JOB}:{jid}",
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

    # job_data host_list: use fqdn for host_data lookups
    self.acct_host_list = [
        h + "." + cfg.get_host_name_ext() for h in (job.host_list or [])
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
        f"{KEY_JOB_HOST_LIST}:{jid}:{_st}:{_et}",
        TIMEOUT_SHORT,
        _host_list_fn,
    ) or []
    print("query time: {0:.1f}".format(time.time() - qtime))

    if len(self.host_list) == 0:
      self.schema = {}
      return

    # Schema: distinct (type, event) for one host (cached)
    etime = time.time()

    def _schema_fn():
      schema_qs = (host_data.objects.filter(**self._base_filter,
                                            host=self.host_list[0]).values(
                                                "type", "event").distinct())
      return _queryset_to_dataframe(schema_qs)

    schema_df = cached_orm(
        f"{KEY_JOB_SCHEMA}:{jid}:{self.host_list[0]}",
        TIMEOUT_SHORT,
        _schema_fn,
    )
    if schema_df is None or schema_df.empty:
      self.schema = {}
    else:
      types = sorted(schema_df["type"].unique().tolist())
      self.schema = {}
      for t in types:
        self.schema[t] = sorted(
            schema_df[schema_df["type"] == t]["event"].unique().tolist())
    print("schema time: {0:.1f}".format(time.time() - etime))

  def _host_data_qs(self, **extra_filters):
    """Base host_data queryset for this job (time range + hosts).

        """
    return host_data.objects.filter(**self._base_filter, **extra_filters)

  def get_host_time_df(self):
    """DataFrame of (host, time) distinct, ordered by host, time.

        """
    from django.db.models import Min

    # Distinct (host, time) in same order as legacy: group by host, time
    qs = (self._host_data_qs().values("host", "time").distinct().order_by(
        "host", "time"))
    return _queryset_to_dataframe(qs)

  def get_aggregate_df(self, typ, val_col, events, conv=1.0):
    """Aggregate val_col (e.g. 'arc' or 'value') for given type and events. Returns DataFrame with columns host, time, sum_val (sum * conv).

        """
    from django.db.models import Sum

    qs = (self._host_data_qs(type=typ, event__in=events).values(
        "host", "time").annotate(sum_val=Sum(val_col)).order_by("host", "time"))
    df = _queryset_to_dataframe(qs)
    if df.empty:
      return df
    if "sum_val" in df.columns:
      df["sum_val"] = df["sum_val"] * conv
    return df

  def get_full_host_data_df(self, columns=None):
    """Full host_data for this job as DataFrame (host, time, type, event, value, etc.).

        """
    cols = columns or ["host", "time", "type", "event", "value", "arc", "delta"]
    qs = (
        self._host_data_qs()
        .values(*cols)
        .order_by("host", "time")
        .iterator(chunk_size=10000)
    )
    return _queryset_to_dataframe(qs)

  def get_llite_delta_by_event(self):
    """Lustre read_bytes/write_bytes sum(delta) by event for this job (cached).

        """
    from django.db.models import Sum

    def _llite_fn():
      qs = (self._host_data_qs(
          type="llite",
          event__in=["read_bytes", "write_bytes"],
      ).values("event").annotate(delta_sum=Sum("delta")).order_by("event"))
      return _queryset_to_dataframe(qs)

    key = f"{KEY_LLITE_DELTA}:{self.jid}"
    result = cached_orm(key, TIMEOUT_SHORT, _llite_fn)
    return result if result is not None else _queryset_to_dataframe(None)

  def close(self):
    """No-op; no connection to close. Kept for context manager compatibility.

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
    """Destructor; call close() if possible."""
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
    """DataFrame of (host, time) distinct, ordered by host, time.

        """
    qs = (self._qs().values("host", "time").distinct().order_by("host", "time"))
    return _queryset_to_dataframe(qs)

  def get_events_units(self):
    """List of (event, unit) for one host.

        """
    if not self.host_list:
      return []
    qs = (self._qs(host=self.host_list[0]).values("event", "unit").distinct())
    df = _queryset_to_dataframe(qs)
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
    """Aggregate metric (e.g. arc) by host and time for the given event; returns DataFrame with sum_val.

        """
    from django.db.models import Sum

    qs = (self._qs(event=event).values(
        "host", "time").annotate(sum_val=Sum(metric)).order_by("host", "time"))
    return _queryset_to_dataframe(qs)


class HostDataProvider:
  """ORM-based provider for host-scoped host_data (one host, time range). Same interface as jid_table for SummaryPlot: jid, host_list, get_host_time_df, get_aggregate_df.

    """

  def __init__(self, host_fqdn, start_time, end_time):
    """Build base filter and schema for one host and time range.

        """
    self.jid = host_fqdn.split(".")[0].replace("-", "_")
    self.host_list = [host_fqdn]
    self.conj = None
    self._base_filter = {
        "host": host_fqdn,
        "time__gte": start_time,
        "time__lte": end_time,
    }
    # Schema: distinct (type, event) for this host
    schema_qs = (host_data.objects.filter(**self._base_filter).values(
        "type", "event").distinct())
    schema_df = _queryset_to_dataframe(schema_qs)
    if schema_df.empty:
      self.schema = {}
    else:
      types = sorted(schema_df["type"].unique().tolist())
      self.schema = {}
      for t in types:
        self.schema[t] = sorted(
            schema_df[schema_df["type"] == t]["event"].unique().tolist())

  def _host_data_qs(self, **extra_filters):
    """Base host_data queryset for this host (time range).

        """
    return host_data.objects.filter(**self._base_filter, **extra_filters)

  def get_host_time_df(self):
    """DataFrame of (host, time) distinct, ordered by host, time.

        """
    qs = (self._host_data_qs().values("host", "time").distinct().order_by(
        "host", "time"))
    return _queryset_to_dataframe(qs)

  def get_aggregate_df(self, typ, val_col, events, conv=1.0):
    """Aggregate val_col for type and events; returns DataFrame with host, time, sum_val (sum * conv).

        """
    from django.db.models import Sum

    qs = (self._host_data_qs(type=typ, event__in=events).values(
        "host", "time").annotate(sum_val=Sum(val_col)).order_by("host", "time"))
    df = _queryset_to_dataframe(qs)
    if not df.empty and "sum_val" in df.columns:
      df["sum_val"] = df["sum_val"] * conv
    return df
