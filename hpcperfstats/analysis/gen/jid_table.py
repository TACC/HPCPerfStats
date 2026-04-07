"""Job-scoped host_data access via Django ORM. Provides jid_table, TypeDetailDataProvider, and HostDataProvider for querying job/host metrics without raw SQL. Uses Redis caching for heavy queries.

"""
import hashlib
import json
import logging
import threading
import time
from datetime import timezone as dt_utc

from django.core.cache import cache

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.gen.utils import queryset_to_dataframe
from hpcperfstats.site.machine.cache_utils import (
    KEY_AGG_DF,
    KEY_JOB,
    KEY_JOB_HOST_LIST,
    KEY_JOB_SCHEMA,
    KEY_HOST_DATA_DF,
    KEY_HOST_SCHEMA,
    KEY_HOST_TIME_DF,
    KEY_JID_HOST_WINDOW_ROW_COUNT,
    KEY_LLITE_DELTA,
    KEY_NFS_FSIO,
    KEY_TYPE_DETAIL_AGG,
    KEY_TYPE_DETAIL_HOST_TIME,
    cached_orm,
    get_site_content_cache_timeout,
    make_cache_key,
    make_cache_key_bounded,
)
from hpcperfstats.site.machine.models import host_data, job_data

# Chunk host__in on host_data for large jobs; single queries with thousands of
# hosts often hit PostgreSQL statement_timeout during metrics and plots.
JID_TABLE_HOST_QUERY_BATCH = 64
# Avoid very large ANY(text[]) binds for row-count probe; chunk these jobs.
JID_TABLE_ROW_COUNT_POSTGRES_ARRAY_MAX_HOSTS = 512


def _iter_acct_host_batches(acct_host_list, batch_size=None):
  """Yield successive host__in subsets of acct_host_list (stable order)."""
  if not acct_host_list:
    return
  bs = max(1, int(batch_size or JID_TABLE_HOST_QUERY_BATCH))
  hosts = list(acct_host_list)
  for i in range(0, len(hosts), bs):
    yield hosts[i:i + bs]


def _count_host_data_rows_for_window(start, end, acct_hosts):
  """Total host_data rows in [start, end] for accounting FQDNs (chunked on non-PostgreSQL)."""
  if not acct_hosts or start is None or end is None:
    return 0

  def _count_chunked():
    total = 0
    for host_chunk in _iter_acct_host_batches(acct_hosts):
      total += host_data.objects.filter(
          time__gte=start,
          time__lte=end,
          host__in=host_chunk,
      ).count()
    return total

  from django.db import connections

  conn = connections["default"]
  if conn.vendor == "postgresql":
    if len(acct_hosts) > JID_TABLE_ROW_COUNT_POSTGRES_ARRAY_MAX_HOSTS:
      return _count_chunked()
    from django.db import connection

    try:
      ops = connection.ops
      tbl = ops.quote_name(host_data._meta.db_table)
      ct = ops.quote_name("time")
      ch = ops.quote_name("host")
      sql = (
          "SELECT COUNT(*) FROM {tbl} WHERE {ct} >= %s AND {ct} <= %s "
          "AND {ch} = ANY(%s::text[])"
      ).format(tbl=tbl, ct=ct, ch=ch)
      with conn.cursor() as cursor:
        cursor.execute(sql, [start, end, list(acct_hosts)])
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
      _logger.warning(
          "jid_table row count PostgreSQL ANY() failed; retrying chunked ORM count",
          exc_info=True,
      )
      try:
        conn.close_if_unusable_or_obsolete()
      except Exception:
        pass
      return _count_chunked()

  return _count_chunked()


def _acct_hosts_cache_fingerprint(acct_hosts):
  blob = "\n".join(sorted(str(h) for h in acct_hosts))
  return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _count_host_data_rows_for_window_cached(jid, start, end, acct_hosts):
  """Exact window COUNT(*) with optional short Django-cache TTL (see conf_parser)."""
  ttl = cfg.get_large_job_window_row_count_cache_ttl()
  if ttl > 0 and jid and start is not None and end is not None:
    key = make_cache_key(
        KEY_JID_HOST_WINDOW_ROW_COUNT,
        jid,
        start.isoformat(),
        end.isoformat(),
        _acct_hosts_cache_fingerprint(acct_hosts),
    )
    try:
      cached = cache.get(key)
    except Exception:
      cached = None
    if cached is not None:
      try:
        return int(cached)
      except (TypeError, ValueError):
        pass
    n = _count_host_data_rows_for_window(start, end, acct_hosts)
    try:
      cache.set(key, int(n), timeout=ttl)
    except Exception:
      pass
    return n
  return _count_host_data_rows_for_window(start, end, acct_hosts)


def _strided_distinct_times_postgresql(start, end, acct_hosts, n_buckets):
  """Up to n_buckets timestamps, one per NTILE bucket over DISTINCT time (ordered)."""
  from django.db import connection

  if not acct_hosts or start is None or end is None or n_buckets < 2:
    return []
  ops = connection.ops
  tbl = ops.quote_name(host_data._meta.db_table)
  ct = ops.quote_name("time")
  ch = ops.quote_name("host")
  sql = (
      "WITH dt AS ("
      " SELECT DISTINCT h.{ct} AS ts FROM {tbl} h"
      " WHERE h.{ct} >= %s AND h.{ct} <= %s AND h.{ch} = ANY(%s::text[])"
      "), bucketed AS ("
      " SELECT ts, NTILE(%s) OVER (ORDER BY ts) AS b FROM dt"
      ") SELECT MAX(ts) FROM bucketed GROUP BY b ORDER BY MAX(ts)"
  ).format(tbl=tbl, ct=ct, ch=ch)
  with connection.cursor() as cursor:
    cursor.execute(sql, [start, end, list(acct_hosts), int(n_buckets)])
    return [r[0] for r in cursor.fetchall() if r and r[0] is not None]


def _strided_distinct_times_date_bin_postgresql(start, end, acct_hosts, n_buckets):
  """Up to ~n_buckets timestamps via ``GROUP BY date_bin`` (PostgreSQL 14+)."""
  from django.db import connection

  if not acct_hosts or start is None or end is None or n_buckets < 2:
    return []
  span_sec = (end - start).total_seconds()
  if span_sec <= 0:
    return [start]
  step_sec = max(span_sec / float(max(n_buckets - 1, 1)), 1e-9)
  ops = connection.ops
  tbl = ops.quote_name(host_data._meta.db_table)
  ct = ops.quote_name("time")
  ch = ops.quote_name("host")
  sql = (
      "SELECT MAX(h.{ct}) AS ts FROM {tbl} h"
      " WHERE h.{ct} >= %s AND h.{ct} <= %s AND h.{ch} = ANY(%s::text[])"
      " GROUP BY date_bin((%s::double precision * interval '1 second'), h.{ct}, %s)"
      " ORDER BY ts"
  ).format(tbl=tbl, ct=ct, ch=ch)
  with connection.cursor() as cursor:
    cursor.execute(
        sql,
        [start, end, list(acct_hosts), step_sec, start],
    )
    return [r[0] for r in cursor.fetchall() if r and r[0] is not None]


def _strided_distinct_times_for_large_job(start, end, acct_hosts, n_buckets):
  """Strided sample timestamps: ``date_bin`` when configured, else NTILE path."""
  if cfg.get_large_job_time_sample_sql_mode() == "date_bin":
    try:
      out = _strided_distinct_times_date_bin_postgresql(
          start, end, acct_hosts, n_buckets)
      if out:
        return out
    except Exception:
      logging.getLogger(__name__).warning(
          "jid_table date_bin strided times failed; falling back to NTILE",
          exc_info=True,
      )
  try:
    return _strided_distinct_times_postgresql(
        start, end, acct_hosts, n_buckets)
  except Exception:
    logging.getLogger(__name__).warning(
        "jid_table NTILE strided times failed; falling back to fixed window points",
        exc_info=True,
    )
    # Keep sampling active even when DB striding paths fail (e.g. statement_timeout)
    # so downstream queries avoid full-window scans for very large jobs.
    if start is None or end is None:
      return []
    if end <= start:
      return [start]
    mid = start + ((end - start) / 2)
    return [start, mid, end]


local_timezone = cfg.get_local_timezone()
_logger = logging.getLogger(__name__)

# Optional instrumentation: count get_aggregate_df entries while building summary plots.
_summary_agg_count_lock = threading.Lock()
_summary_agg_count = 0
_summary_agg_counting = False


def begin_summary_aggregate_counting():
    """Start counting ``get_aggregate_df`` calls (thread-safe)."""
    global _summary_agg_counting, _summary_agg_count
    with _summary_agg_count_lock:
        _summary_agg_count = 0
        _summary_agg_counting = True


def end_summary_aggregate_counting():
    """Stop counting and return the number of ``get_aggregate_df`` calls seen."""
    global _summary_agg_counting, _summary_agg_count
    with _summary_agg_count_lock:
        _summary_agg_counting = False
        return _summary_agg_count


def _incr_summary_aggregate_count_if_active():
    global _summary_agg_count
    if not _summary_agg_counting:
        return
    with _summary_agg_count_lock:
        if _summary_agg_counting:
            _summary_agg_count += 1


def _ensure_tz(dt):
  """Ensure datetime is timezone-aware in local_timezone for display.

    """
  if dt is None:
    return None
  if dt.tzinfo is None:
    from django.utils import timezone as django_tz
    dt = django_tz.make_aware(dt, dt_utc.utc)
  return dt.astimezone(local_timezone)


def _normalize_job_accounting_host_list(raw):
  """Coerce ``job_data.host_list`` (ArrayField) to a list of short hostnames.

  Defensive: corrupted cache/ORM values can surface as a non-list (e.g. a lone
  ``datetime``), which must not be iterated for FQDN construction.
  """
  if raw is None:
    return []
  if isinstance(raw, (list, tuple)):
    return [str(h) for h in raw]
  return []


def _unpack_cached_job_window_row(row):
  """Return ``(host_list, start_time, end_time)`` from a cached jid row.

  Prefer a ``values_list`` tuple (pickle-safe). Older deployments may still
  have a cached partial ``job_data`` instance.
  """
  if row is None:
    return None, None, None
  if isinstance(row, job_data):
    return row.host_list, row.start_time, row.end_time
  if isinstance(row, (tuple, list)) and len(row) == 3:
    return row[0], row[1], row[2]
  return None, None, None


def gpu_acct_window_for_job_data(job):
  """Return ``(start_time, end_time, acct_host_list)`` for GPU-style host_data queries.

  Uses the same FQDN construction as :class:`jid_table` without distinct-host
  discovery, schema scans, or large-job time sampling (those are expensive and
  unnecessary for rolled-up GPU aggregates that only need accounting hosts and
  the job window).
  """
  hl_raw = getattr(job, "host_list", None)
  st = getattr(job, "start_time", None)
  et = getattr(job, "end_time", None)
  if st is None and et is None:
    return _ensure_tz(st), _ensure_tz(et), []
  acct_host_list = [
      str(h) + "." + cfg.get_host_name_ext()
      for h in _normalize_job_accounting_host_list(hl_raw)
  ]
  return _ensure_tz(st), _ensure_tz(et), acct_host_list


def _normalize_host_data_schema_label(value):
  """Coerce ``host_data.type`` / ``event`` cell to a string safe for pandas ``.unique()``."""
  if value is None:
    return None
  if isinstance(value, bytes):
    return value.decode("utf-8", errors="replace")
  if isinstance(value, str):
    return value
  if isinstance(value, (list, dict, tuple)):
    try:
      return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except TypeError:
      return str(value)
  return str(value)


def _coerce_jid_table_schema_dataframe(df):
  """Normalize schema frame so ``type`` / ``event`` are hashable strings (no list cells)."""
  if df is None or df.empty or "type" not in df.columns:
    return df
  out = df.copy()
  out["type"] = out["type"].map(_normalize_host_data_schema_label)
  if "event" in out.columns:
    out["event"] = out["event"].map(_normalize_host_data_schema_label)
  out = out[out["type"].notna()]
  return out


class jid_table:
  """Job-scoped view of job_data and host_data using Django ORM. No raw connection or temp tables; all data via ORM.

    """

  def __init__(self, jid):
    """Build job-scoped filter from job_data and populate host_list and schema from host_data.

        """
    _logger.debug("Initializing jid_table for job %s", jid)

    self.jid = jid
    self._large_job_plot_cache_token = "full"

    try:
      row = cached_orm(
          make_cache_key(KEY_JOB, jid),
          get_site_content_cache_timeout(),
          lambda: job_data.objects.filter(jid=jid).values_list(
              "host_list", "start_time", "end_time"
          ).first(),
      )
    except Exception:
      row = None

    if row is None:
      self.acct_host_list = []
      self.host_list = []
      self.schema = {}
      self.start_time = None
      self.end_time = None
      self._base_filter = {}
      return

    hl_raw, st, et = _unpack_cached_job_window_row(row)
    if st is None and et is None:
      self.acct_host_list = []
      self.host_list = []
      self.schema = {}
      self.start_time = None
      self.end_time = None
      self._base_filter = {}
      return

    # job_data host_list: use fqdn for host_data lookups (cast to str for varchar comparison)
    self.acct_host_list = [
        str(h) + "." + cfg.get_host_name_ext()
        for h in _normalize_job_accounting_host_list(hl_raw)
    ]
    self.start_time = _ensure_tz(st)
    self.end_time = _ensure_tz(et)
    self._base_filter = {
        "time__gte": self.start_time,
        "time__lte": self.end_time,
        "host__in": self.acct_host_list,
    }

    # Distinct hosts that actually have host_data in range (cached)
    qtime = time.time()

    def _host_list_fn():
      found = set()
      for host_chunk in _iter_acct_host_batches(self.acct_host_list):
        host_qs = (
            host_data.objects.filter(
                time__gte=self._base_filter["time__gte"],
                time__lte=self._base_filter["time__lte"],
                host__in=host_chunk,
            )
            .values_list("host", flat=True)
            .distinct()
        )
        found.update(host_qs)
      return list(found)

    _st = self.start_time.isoformat() if self.start_time else ""
    _et = self.end_time.isoformat() if self.end_time else ""
    self.host_list = cached_orm(
        make_cache_key(KEY_JOB_HOST_LIST, jid, _st, _et),
        get_site_content_cache_timeout(),
        _host_list_fn,
    ) or []
    _logger.debug("jid_table host_list query time: %.1fs", time.time() - qtime)

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
      return _coerce_jid_table_schema_dataframe(
          pd.DataFrame(rows, columns=["type", "event"]))

    schema_df = cached_orm(
        make_cache_key(KEY_JOB_SCHEMA, jid, self.host_list[0]),
        get_site_content_cache_timeout(),
        _schema_fn,
    )
    schema_df = _coerce_jid_table_schema_dataframe(schema_df)
    if schema_df is None or schema_df.empty or "type" not in schema_df.columns:
      self.schema = {}
    else:
      types = sorted(schema_df["type"].unique().tolist())
      self.schema = {}
      for t in types:
        self.schema[t] = sorted(
            schema_df[schema_df["type"] == t]["event"].unique().tolist())
    _logger.debug("jid_table schema time: %.1fs", time.time() - etime)
    self._apply_large_job_time_sampling_if_needed()

  def _host_data_time_filter_kwargs(self):
    """Time scope for host_data queries: full window or sampled ``time__in``."""
    if not self._base_filter:
      return {}
    if "time__in" in self._base_filter:
      return {"time__in": self._base_filter["time__in"]}
    return {
        "time__gte": self._base_filter["time__gte"],
        "time__lte": self._base_filter["time__lte"],
    }

  def _apply_large_job_time_sampling_if_needed(self):
    """If row count exceeds threshold, restrict queries to NTILE-strided timestamps."""
    self._large_job_plot_cache_token = "full"
    if not self.acct_host_list or not self.start_time or not self.end_time:
      return
    threshold = cfg.get_large_job_host_data_row_threshold()
    try:
      n = _count_host_data_rows_for_window_cached(
          self.jid, self.start_time, self.end_time, self.acct_host_list)
    except Exception as exc:
      _logger.warning(
          "jid_table large-job row count failed jid=%s: %s",
          self.jid,
          exc,
      )
      return
    if n <= threshold:
      return
    n_buckets = cfg.get_large_job_time_buckets()
    try:
      sampled = _strided_distinct_times_for_large_job(
          self.start_time, self.end_time, self.acct_host_list, n_buckets)
    except Exception as exc:
      _logger.warning(
          "jid_table strided times failed jid=%s: %s",
          self.jid,
          exc,
      )
      return
    if not sampled:
      return
    self._base_filter = {
        "time__in": sampled,
        "host__in": self.acct_host_list,
    }
    self._large_job_plot_cache_token = "lb{}".format(len(sampled))
    _logger.info(
        "jid_table large-job time sampling jid=%s rows~%s bucket_times=%s",
        self.jid,
        n,
        len(sampled),
    )

  def _host_data_qs(self, **extra_filters):
    """Base host_data queryset for this job (time range + hosts).

        """
    if not self._base_filter:
      return host_data.objects.none()
    tkw = self._host_data_time_filter_kwargs()
    return host_data.objects.filter(
        host__in=self._base_filter["host__in"],
        **tkw,
        **extra_filters,
    )

  def _full_host_data_rows_batched(self, cols):
    """values_list rows for the job window, chunking host__in for large node counts."""
    rows = []
    if not self.acct_host_list or not self._base_filter:
      return rows
    tkw = self._host_data_time_filter_kwargs()
    for host_chunk in _iter_acct_host_batches(self.acct_host_list):
      qs = (
          host_data.objects.filter(
              host__in=host_chunk,
              **tkw,
          )
          .values_list(*cols)
          .order_by("host", "time")
      )
      for r in qs:
        if not isinstance(r, (list, tuple)):
          continue
        if len(r) < len(cols):
          continue
        rows.append(tuple(r[:len(cols)]))
    return rows

  def get_host_time_df(self):
    """DataFrame of (host, time) distinct, ordered by host, time (cached).

        """
    def _fn():
      import pandas as pd

      if not self.acct_host_list:
        return pd.DataFrame(columns=["host", "time"])
      tkw = self._host_data_time_filter_kwargs()
      frames = []
      for host_chunk in _iter_acct_host_batches(self.acct_host_list):
        qs = (
            host_data.objects.filter(
                host__in=host_chunk,
                **tkw,
            )
            .values("host", "time")
            .distinct()
            .order_by("host", "time")
        )
        frames.append(queryset_to_dataframe(qs))
      if not frames:
        return pd.DataFrame(columns=["host", "time"])
      out = pd.concat(frames, ignore_index=True)
      if out.empty or not {"host", "time"}.issubset(out.columns):
        return pd.DataFrame(columns=["host", "time"])
      return out.sort_values(["host", "time"]).reset_index(drop=True)

    key = make_cache_key(
        KEY_HOST_TIME_DF, self.jid, self._large_job_plot_cache_token)
    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
    return result if result is not None else queryset_to_dataframe(None)

  def get_aggregate_df(self, typ, val_col, events, conv=1.0):
    """Aggregate val_col (e.g. 'arc' or 'value') for given type and events. Returns DataFrame with columns host, time, sum_val (sum * conv). Result is cached per (jid, typ, val_col, events).
        """
    _incr_summary_aggregate_count_if_active()
    events_key = ":".join(sorted(events))

    def _fn_pandas_groupby():
      hosts = [str(h) for h in self._base_filter.get("host__in") or []]
      import pandas as pd

      if not hosts:
        return pd.DataFrame(columns=["host", "time", "sum_val"])

      tkw = self._host_data_time_filter_kwargs()
      frames = []
      for host_chunk in _iter_acct_host_batches(hosts):
        qs = host_data.objects.filter(
            host__in=host_chunk,
            **tkw,
            type=typ,
            event__in=list(events),
        ).values("host", "time", val_col)
        df_raw = queryset_to_dataframe(qs)
        if (
            df_raw.empty
            or "host" not in df_raw.columns
            or "time" not in df_raw.columns
            or val_col not in df_raw.columns
        ):
          continue
        df_grouped = (
            df_raw.groupby(["host", "time"], as_index=False)[val_col]
            .sum()
            .rename(columns={val_col: "sum_val"})
        )
        frames.append(df_grouped)
      if not frames:
        return pd.DataFrame(columns=["host", "time", "sum_val"])
      df_all = pd.concat(frames, ignore_index=True)
      df_all = (
          df_all.groupby(["host", "time"], as_index=False)["sum_val"]
          .sum()
          .sort_values(["host", "time"])
      )
      df_all["sum_val"] = df_all["sum_val"] * conv
      return df_all

    def _fn():
      import pandas as pd

      hosts = [str(h) for h in self._base_filter.get("host__in") or []]
      if not hosts:
        return pd.DataFrame(columns=["host", "time", "sum_val"])

      _ALLOWED = ("arc", "value", "delta")
      if val_col not in _ALLOWED:
        return _fn_pandas_groupby()

      try:
        from django.db.models import Sum, Value
        from django.db.models.functions import Coalesce

        tkw = self._host_data_time_filter_kwargs()
        frames = []
        for host_chunk in _iter_acct_host_batches(hosts):
          qs_sql = (
              host_data.objects.filter(
                  host__in=host_chunk,
                  **tkw,
                  type=typ,
                  event__in=list(events),
              )
              .values("host", "time")
              .annotate(sum_val=Coalesce(Sum(val_col), Value(0)))
              .order_by("host", "time")
          )
          frames.append(queryset_to_dataframe(qs_sql))
        if not frames:
          return pd.DataFrame(columns=["host", "time", "sum_val"])
        df_sql = pd.concat(frames, ignore_index=True)
        if df_sql.empty or "sum_val" not in df_sql.columns:
          return pd.DataFrame(columns=["host", "time", "sum_val"])
        df_sql = (
            df_sql.groupby(["host", "time"], as_index=False)["sum_val"]
            .sum()
            .sort_values(["host", "time"])
        )
        df_sql["sum_val"] = df_sql["sum_val"].astype("float64") * conv
        return df_sql
      except Exception:
        _logger.debug(
            "get_aggregate_df SQL aggregate failed jid=%s typ=%s; using pandas",
            self.jid,
            typ,
            exc_info=True,
        )
        return _fn_pandas_groupby()

    key = make_cache_key_bounded(
        KEY_AGG_DF, self.jid, typ, val_col, events_key,
        self._large_job_plot_cache_token,
    )
    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
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

      rows = self._full_host_data_rows_batched(cols)
      return pd.DataFrame(rows, columns=cols)

    def _fn():
      import pandas as pd

      rows = self._full_host_data_rows_batched(cols)
      return pd.DataFrame(rows, columns=cols)

    key = make_cache_key(
        KEY_HOST_DATA_DF, self.jid, self._large_job_plot_cache_token)
    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
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

    key = make_cache_key(
        KEY_LLITE_DELTA, self.jid, self._large_job_plot_cache_token)
    result = cached_orm(key, get_site_content_cache_timeout(), _llite_fn)
    return result if result is not None else queryset_to_dataframe(None)

  def get_nfs_delta_totals_mb(self):
    """Aggregate NFS client byte counters (monitor type `nfs`) to total read/write MB.

    Uses the same counters as the monitor's `nfs.c` BYTE_KEYS: normal/direct/server
    read and write. Intended for the job detail File System when Lustre `llite`
    stats are not available.
    """
    from django.db.models import Sum

    nfs_read_events = ("normal_read", "direct_read", "server_read")
    nfs_write_events = ("normal_write", "direct_write", "server_write")
    all_events = list(nfs_read_events) + list(nfs_write_events)

    def _nfs_fn():
      qs = (
          self._host_data_qs(
              type="nfs",
              event__in=all_events,
          ).values("event").annotate(delta_sum=Sum("delta")).order_by("event"))
      df = queryset_to_dataframe(qs)
      if df.empty or "delta_sum" not in df.columns:
        return None
      read_total = 0.0
      write_total = 0.0
      for _, row in df.iterrows():
        ev = row.get("event")
        ds = float(row.get("delta_sum") or 0)
        if ev in nfs_read_events:
          read_total += ds
        elif ev in nfs_write_events:
          write_total += ds
      if read_total == 0.0 and write_total == 0.0:
        return None
      return [read_total / (1024 * 1024), write_total / (1024 * 1024)]

    key = make_cache_key(
        KEY_NFS_FSIO, self.jid, self._large_job_plot_cache_token)
    result = cached_orm(key, get_site_content_cache_timeout(), _nfs_fn)
    return result

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
  """ORM-based provider for type-detail view: host_data scoped by job start/end and accounting host_list (no host_data.jid).

    """

  def __init__(self, jid, type_name, start_time, end_time, host_list):
    """Build base filter for type_name, time range, and optional host_list. ``jid`` is only for cache keys / API identity.

        """
    self.jid = jid
    self.type_name = type_name
    self.start_time = start_time
    self.end_time = end_time
    self.host_list = list(host_list) if host_list else []

  def _qs(self, **extra):
    """Base host_data queryset for this provider (type, time range, optional host_list).

        """
    flt = {
        "type": self.type_name,
        "time__gte": self.start_time,
        "time__lte": self.end_time,
    }
    if self.host_list:
      flt["host__in"] = self.host_list
    return host_data.objects.filter(**flt, **extra)

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

    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
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

    result = cached_orm(key, get_site_content_cache_timeout(), _fn)
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

    self.schema = cached_orm(cache_key, get_site_content_cache_timeout(), _schema_fn) or {}

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
    _incr_summary_aggregate_count_if_active()
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
