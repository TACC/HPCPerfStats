"""
API-only helpers for building job sample DataFrames.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from .api_client import ApiClient
from .api_key_cache import load_cached_api_key
from .config import get_api_base_url


def _job_metadata_columns(job_data: dict[str, Any]) -> dict[str, Any]:
  """
  Internal helper to handle job metadata columns.
  
  Args:
    job_data (dict[str, Any]): Mapping for job data.
  
  Returns:
    dict[str, Any]: dict[str, Any] produced by this call.
  
  Examples:
    >>> _job_metadata_columns({})  # doctest: +SKIP
  """
  return {
      "jid": job_data.get("jid"),
      "jobname": job_data.get("jobname"),
      "username": job_data.get("username"),
      "account": job_data.get("account"),
      "queue": job_data.get("queue"),
      "start_time": job_data.get("start_time"),
      "end_time": job_data.get("end_time"),
      "runtime": job_data.get("runtime"),
      "ncores": job_data.get("ncores"),
      "nhosts": job_data.get("nhosts"),
      "state": job_data.get("state"),
  }


def get_job_full_dataframe(
  jid: str,
  api_url: Optional[str] = None,
  api_key: Optional[str] = None,
  verify_tls: bool = True,
) -> pd.DataFrame:
  """
  Return an API-derived DataFrame with all available per-type samples +.
  
    metadata.
  
  Data is assembled from:
  - `/jobs/{jid}/` for job metadata and available type schema
  - `/jobs/{jid}/{type_name}/` for per-type stats_data rows and schema
  
  Args:
    jid (str): String for jid.
    api_url (Optional[str]): Api url, or None when absent.
    api_key (Optional[str]): Api key, or None when absent.
    verify_tls (bool): Boolean flag for verify tls.
  
  Returns:
    pd.DataFrame: Result DataFrame, or None when nothing usable remains.
  
  Raises:
    RuntimeError: Raised when ``get_job_full_dataframe`` hits a ``RuntimeError``
    failure path.
  
  Examples:
    >>> get_job_full_dataframe("x", None, None, True)  # doctest: +SKIP
  """
  base_url = api_url or get_api_base_url() or "http://localhost:8000/api/"
  resolved_api_key = api_key or load_cached_api_key(base_url)
  client = ApiClient(
      base_url=base_url,
      api_key=resolved_api_key,
      verify_tls=verify_tls,
      timeout=60,
  )

  detail = client.get_json(f"jobs/{jid}/")
  if not detail.ok or not isinstance(detail.data, dict):
    raise RuntimeError(f"Failed to fetch job detail for jid={jid}: {detail.error}")

  job_data = detail.data.get("job_data") or {}
  schema_by_type = detail.data.get("schema") or {}
  if not isinstance(schema_by_type, dict):
    schema_by_type = {}

  metadata = _job_metadata_columns(job_data)
  frames: list[pd.DataFrame] = []

  for type_name in sorted(schema_by_type.keys()):
    type_result = client.get_json(f"jobs/{jid}/{type_name}/")
    if not type_result.ok or not isinstance(type_result.data, dict):
      continue
    type_payload = type_result.data
    stats_data = type_payload.get("stats_data") or []
    columns = type_payload.get("schema") or []
    if not stats_data or not columns:
      continue
    rows = []
    for item in stats_data:
      if not isinstance(item, list) or len(item) != 2:
        continue
      dt_value, values = item
      if not isinstance(values, list) or len(values) != len(columns):
        continue
      row = {"dt": dt_value, "type_name": type_name}
      row.update({col: val for col, val in zip(columns, values)})
      rows.append(row)
    if not rows:
      continue
    frame = pd.DataFrame(rows)
    frame["dt_seconds"] = pd.to_timedelta(frame["dt"], errors="coerce").dt.total_seconds()
    for key, val in metadata.items():
      frame[key] = val
    frames.append(frame)

  if not frames:
    return pd.DataFrame(columns=["jid", "type_name", "dt", "dt_seconds"])
  return pd.concat(frames, ignore_index=True)
