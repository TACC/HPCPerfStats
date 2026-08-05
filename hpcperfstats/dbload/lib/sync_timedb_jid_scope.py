"""
Resolve ``job_data`` scope for ``sync_timedb.py --jid`` ingest-only runs.

Does not import the analysis metrics ``jid_table`` stack — FQDN suffix rules
mirror ``jid_table._as_host_data_fqdn`` using ``conf_parser.get_host_name_ext``.

Attributes:
  JID_WINDOW_PAD: Attribute.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple

from hpcperfstats.dbload.lib import conf_parser as cfg

JID_WINDOW_PAD = timedelta(hours=1)


@dataclass(frozen=True)
class JobIngestScope:
  """
  Hosts and padded time window for surgical archive ingest.
  
  Neighbor ±1 files beyond the pad are applied at host-scoped discover/filter.
  
  Attributes:
    end_time: Attribute.
    hosts: Attribute.
    jid: Attribute.
    start_time: Attribute.
    window_end: Attribute.
    window_start: Attribute.
  """

  jid: str
  hosts: Tuple[str, ...]
  window_start: datetime
  window_end: datetime
  start_time: datetime
  end_time: Optional[datetime]


class JobIngestScopeError(ValueError):
  """
  Missing job, empty hosts, or invalid timing for ``--jid``.
  """


def _host_data_suffix() -> str:
  """
  Internal helper to handle host data suffix.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _host_data_suffix()  # doctest: +SKIP
  """
  ext = str(cfg.get_host_name_ext() or "").strip().lstrip(".")
  return "." + ext if ext else ""


def as_host_data_fqdn(host: object) -> str:
  """
  Return host in archive / host_data FQDN form (no duplicate suffix).
  
  Args:
    host (object): Host.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> as_host_data_fqdn(None)  # doctest: +SKIP
  """
  host_s = str(host or "").strip()
  if not host_s:
    return ""
  suffix = _host_data_suffix()
  if not suffix:
    return host_s
  if host_s.lower().endswith(suffix.lower()):
    return host_s
  return host_s + suffix


def normalize_job_host_list(raw: object) -> List[str]:
  """
  Coerce ``job_data.host_list`` (ArrayField or defensive shapes) to short names.
  
  Args:
    raw (object): Raw.
  
  Returns:
    List[str]: List[str] produced by this call.
  
  Examples:
    >>> normalize_job_host_list(None)  # doctest: +SKIP
  """
  if raw is None:
    return []
  if isinstance(raw, datetime):
    return []
  if isinstance(raw, (str, bytes)):
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    parts = [p.strip() for p in text.replace(",", " ").split() if p.strip()]
    return parts
  if isinstance(raw, (list, tuple, set)):
    out: List[str] = []
    for item in raw:
      if isinstance(item, (list, tuple, set)):
        out.extend(normalize_job_host_list(item))
      else:
        s = str(item or "").strip()
        if s:
          out.append(s)
    return out
  return []


def build_acct_host_fqdns(raw_host_list: object) -> List[str]:
  """
  Build unique FQDN host directory names for archive discovery.
  
  Args:
    raw_host_list (object): Raw host list.
  
  Returns:
    List[str]: List[str] produced by this call.
  
  Examples:
    >>> build_acct_host_fqdns(None)  # doctest: +SKIP
  """
  seen = set()
  out: List[str] = []
  for h in normalize_job_host_list(raw_host_list):
    fqdn = as_host_data_fqdn(h)
    if not fqdn or fqdn in seen:
      continue
    seen.add(fqdn)
    out.append(fqdn)
  return out


def _ensure_aware_utc(dt: datetime) -> datetime:
  """
  Internal helper to ensure the aware utc.
  
  Args:
    dt (datetime): Dt.
  
  Returns:
    datetime: datetime produced by this call.
  
  Examples:
    >>> _ensure_aware_utc(None)  # doctest: +SKIP
  """
  if dt.tzinfo is None:
    return dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(timezone.utc)


def padded_job_window(
  start_time: datetime,
  end_time: Optional[datetime],
  *,
  now: Optional[datetime] = None,
  pad: timedelta = JID_WINDOW_PAD,
) -> Tuple[datetime, datetime]:
  """
  Return ``(start - pad, end + pad)``; null end uses ``now + pad`` as end.
  
  Args:
    start_time (datetime): Start time.
    end_time (Optional[datetime]): End time, or None when absent.
    now (Optional[datetime]): Now, or None when absent.
    pad (timedelta): Pad.
  
  Returns:
    Tuple[datetime, datetime]: Tuple[datetime, datetime] produced by this
    call.
  
  Raises:
    JobIngestScopeError: Raised when ``padded_job_window`` hits a
    ``JobIngestScopeError`` failure path.
  
  Examples:
    >>> padded_job_window(None, None, None, None)  # doctest: +SKIP
  """
  if start_time is None:
    raise JobIngestScopeError("job start_time is required")
  start = _ensure_aware_utc(start_time)
  if end_time is None:
    ref = now if now is not None else datetime.now(timezone.utc)
    end = _ensure_aware_utc(ref)
  else:
    end = _ensure_aware_utc(end_time)
  if end < start:
    end = start
  return start - pad, end + pad


def resolve_job_ingest_scope(
  jid: str,
  *,
  now: Optional[datetime] = None,
) -> JobIngestScope:
  """
  Load ``job_data`` and return FQDN hosts + ±1h padded window.
  
  Raises ``JobIngestScopeError`` when the job is missing or has no hosts.
  
  Args:
    jid (str): String for jid.
    now (Optional[datetime]): Now, or None when absent.
  
  Returns:
    JobIngestScope: JobIngestScope produced by this call.
  
  Raises:
    JobIngestScopeError: Raised when ``resolve_job_ingest_scope`` hits a
    ``JobIngestScopeError`` failure path.
  
  Examples:
    >>> resolve_job_ingest_scope("x", None)  # doctest: +SKIP
  """
  from hpcperfstats.site.lib.machine.models import job_data

  jid_s = str(jid or "").strip()
  if not jid_s:
    raise JobIngestScopeError("empty jid")
  try:
    job = job_data.objects.only(
        "jid", "host_list", "start_time", "end_time",
    ).get(jid=jid_s)
  except job_data.DoesNotExist as exc:
    raise JobIngestScopeError("job_data not found jid=%s" % jid_s) from exc

  hosts = build_acct_host_fqdns(job.host_list)
  if not hosts:
    raise JobIngestScopeError("empty host_list jid=%s" % jid_s)

  end_raw = getattr(job, "end_time", None)
  window_start, window_end = padded_job_window(
      job.start_time, end_raw, now=now,
  )
  return JobIngestScope(
      jid=jid_s,
      hosts=tuple(hosts),
      window_start=window_start,
      window_end=window_end,
      start_time=_ensure_aware_utc(job.start_time),
      end_time=None if end_raw is None else _ensure_aware_utc(end_raw),
  )


def parse_sync_timedb_jid_cli_arg(
  argv: Optional[Sequence[str]] = None,
) -> Tuple[ Optional[str], Optional[str], ]:
  """
  Parse ``--jid`` / ``--jid=`` from argv.
  
    Returns ``(jid, error)``:
    - ``(None, None)`` — not a ``--jid`` invocation
    - ``(jid, None)`` — one-shot jid ingest
    - ``(None, message)`` — usage / mutual-exclusion error
  
  Args:
    argv (Optional[Sequence[str]]): Argv, or None when absent.
  
  Returns:
    Tuple[ Optional[str], Optional[str], ]: Tuple[ Optional[str],
    Optional[str], ] produced by this call.
  
  Examples:
    >>> parse_sync_timedb_jid_cli_arg(None)  # doctest: +SKIP
  """
  args = list(argv[1:] if argv else [])
  jid = None
  rest: List[str] = []
  i = 0
  while i < len(args):
    a = args[i]
    if a == "--jid":
      if i + 1 >= len(args) or str(args[i + 1]).startswith("-"):
        return None, "usage: sync_timedb.py --jid <JID>"
      jid = str(args[i + 1]).strip()
      i += 2
      continue
    if a.startswith("--jid="):
      jid = a.split("=", 1)[1].strip()
      i += 1
      continue
    rest.append(a)
    i += 1
  if jid is None:
    return None, None
  if not jid:
    return None, "usage: sync_timedb.py --jid <JID> (empty jid)"
  if rest:
    return None, (
        "sync_timedb.py --jid cannot be combined with other arguments: {0}"
        .format(" ".join(rest))
    )
  return jid, None
