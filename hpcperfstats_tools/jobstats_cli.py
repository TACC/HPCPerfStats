#!/usr/bin/env python
"""Text CLI summary for a single Slurm job.

This script prints a job-efficiency style report similar to the
Princeton `jobstats` tool, using the same DB and metrics that power the
HPCPerfStats web UI.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from .api_client import ApiClient
from .api_key_cache import (
    API_KEY_CACHE,
    api_key_help_url,
    load_cached_api_key,
    save_cached_api_key,
)
from .config import get_api_base_url

BAR_WIDTH = 60


def _format_timedelta(seconds: Optional[float]) -> str:
  """Return human-readable D-HH:MM:SS for a seconds value."""
  if seconds is None:
    return "N/A"
  try:
    total = int(seconds)
  except (TypeError, ValueError, OverflowError):
    return "N/A"
  if total < 0:
    total = 0
  td = timedelta(seconds=total)
  days = td.days
  hours, rem = divmod(td.seconds, 3600)
  minutes, secs = divmod(rem, 60)
  if days:
    return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"
  return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _bar(percentage: Optional[float]) -> str:
  """Return an ASCII bar for a 0–100 percentage."""
  if percentage is None:
    return "[no data]".ljust(BAR_WIDTH + 7)
  try:
    pct = float(percentage)
  except (TypeError, ValueError):
    return "[no data]".ljust(BAR_WIDTH + 7)
  pct = max(0.0, min(pct, 100.0))
  filled = int(round(BAR_WIDTH * pct / 100.0))
  bar = "|" * filled + " " * (BAR_WIDTH - filled)
  return f"[{bar} {pct:3.0f}%]"


def _compute_metrics(job_data: Dict[str, object],
                     metrics_list) -> Dict[str, object]:
  """Collect selected metrics and useful aggregates for a job."""
  metrics_by_name = {
      m["metric"]: m for m in metrics_list if m.get("metric")
  }

  cpu_util_pct: Optional[float] = None
  ncores = job_data.get("ncores") or 0
  if "avg_cpuusage" in metrics_by_name and ncores:
    value = metrics_by_name["avg_cpuusage"].get("value") or 0.0
    try:
      cpu_util_pct = 100.0 * float(value) / float(ncores)
    except (TypeError, ValueError, ZeroDivisionError):
      cpu_util_pct = None

  gpu_util_pct: Optional[float] = None
  if "avg_gpuutil" in metrics_by_name:
    try:
      gpu_util_pct = float(metrics_by_name["avg_gpuutil"].get("value"))
    except (TypeError, ValueError):
      gpu_util_pct = None

  mem_hwm_gib: Optional[float] = None
  if "mem_hwm" in metrics_by_name:
    try:
      mem_hwm_gib = float(metrics_by_name["mem_hwm"].get("value"))
    except (TypeError, ValueError):
      mem_hwm_gib = None

  return {
      "cpu_util_pct": cpu_util_pct,
      "gpu_util_pct": gpu_util_pct,
      "mem_hwm_gib": mem_hwm_gib,
      "metrics_by_name": metrics_by_name,
  }


def _get_json(client: ApiClient,
              base_url: str,
              path: str,
              verify: bool,
              api_key: Optional[str]) -> Tuple[Optional[Dict[str, object]], int]:
  result = client.get_json(path)
  url = base_url.rstrip("/") + "/" + path.lstrip("/")
  if result.status_code == 0:
    print(f"Failed to contact API at {url}: {result.error}")
    return None, 0
  if result.status_code == 404:
    return None, result.status_code
  if result.status_code in (401, 403):
    help_url = api_key_help_url(base_url)
    print(
        "Authentication with the HPCPerfStats API failed "
        f"({result.status_code})."
    )
    print(
        "Obtain an API key from:\n"
        f"  {help_url}\n"
        "Then run this command again with --api-key.\n"
        f"The key will be cached in {API_KEY_CACHE}."
    )
    return None, result.status_code
  if not result.ok:
    print(f"API request failed ({result.status_code}) for {url}: {result.error}")
    return None, result.status_code
  if not isinstance(result.data, dict):
    print(f"API returned invalid JSON for {url}")
    return None, result.status_code
  return result.data, result.status_code


def print_jobstats(jid: str,
                   api_url: str,
                   verify_tls: bool,
                   api_key: Optional[str]) -> int:
  """Fetch job + metrics via REST API and print a jobstats-style summary."""
  client = ApiClient(
      base_url=api_url,
      api_key=api_key,
      verify_tls=verify_tls,
      timeout=30,
  )

  detail, status = _get_json(
      client, api_url, f"jobs/{jid}/", verify_tls, api_key
  )
  if detail is None:
    if status == 0:
      return 1
    # Auth and connectivity were already handled in _get_json.
    print(f"No job found with id {jid}")
    return 1

  # At this point authentication to the API succeeded; persist URL->key mapping.
  if api_key:
    save_cached_api_key(api_url, api_key)

  home, _ = _get_json(client, api_url, "home/", verify_tls, api_key)
  if home is None:
    home = {}
  hostname = home.get("machine_name", "-")

  job = detail.get("job_data") or {}
  metrics_list = detail.get("metrics_list") or []

  runtime = job.get("runtime") or 0.0
  timelimit = job.get("timelimit")
  runtime_str = _format_timedelta(runtime)
  timelimit_str = _format_timedelta(timelimit)

  queue_wait_hours: Optional[float] = None
  start_time = job.get("start_time")
  submit_time = job.get("submit_time")
  if start_time and submit_time:
    try:
      st = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
      sub = datetime.fromisoformat(str(submit_time).replace("Z", "+00:00"))
      delta = st - sub
      queue_wait_hours = delta.total_seconds() / 3600.0
    except (ValueError, TypeError):
      queue_wait_hours = None

  m = _compute_metrics(job, metrics_list)

  width = 79
  print("=" * width)
  print("Slurm Job Statistics".center(width))
  print("=" * width)
  print(f"{'Job ID:':>14} {job.get('jid')}")
  print(
      f"{'User/Account:':>14} {job.get('username')}/"
      f"{job.get('account') or '-'}"
  )
  print(f"{'Job Name:':>14} {job.get('jobname') or '-'}")
  print(f"{'State:':>14} {job.get('state') or '-'}")
  print(f"{'Nodes:':>14} {job.get('nhosts') or '-'}")
  print(f"{'CPU Cores:':>14} {job.get('ncores') or '-'}")
  print(
      f"{'QOS/Partition:':>14} "
      f"{job.get('QOS') or job.get('queue') or '-'}"
  )
  print(f"{'Cluster:':>14} {hostname}")
  print(f"{'Start Time:':>14} {job.get('start_time')}")
  print(f"{'Run Time:':>14} {runtime_str}")
  print(f"{'Time Limit:':>14} {timelimit_str}")
  if queue_wait_hours is not None:
    print(f"{'Queue Wait:':>14} {queue_wait_hours:0.2f} hours")

  print()
  print("Overall Utilization".center(width))
  print("=" * width)
  print(f"  CPU utilization   {_bar(m['cpu_util_pct'])}")
  print(f"  GPU utilization   {_bar(m['gpu_util_pct'])}")
  if m["mem_hwm_gib"] is not None:
    print(f"  Memory HWM        {m['mem_hwm_gib']:.2f} GiB")

  other = [
      v for k, v in sorted(m["metrics_by_name"].items())
      if k not in {"avg_cpuusage", "avg_gpuutil", "mem_hwm"}
  ]
  if other:
    print()
    print("Selected Metrics".center(width))
    print("=" * width)
    for metric in other:
      value = metric.get("value")
      units = metric.get("units") or ""
      if value is not None:
        display = f"{value!r} {units}".strip()
      else:
        display = metric.get("no_data_reason") or "No data"
      print(f"  {metric.get('metric', ''):20s} {display}")

  return 0


def main(argv: Optional[list[str]] = None) -> int:
  parser = argparse.ArgumentParser(
      description="Print an efficiency summary for a single Slurm job.",
      formatter_class=argparse.RawDescriptionHelpFormatter,
      epilog="""
Environment variables:
  HPCPERFSTATS_TOOLS_INI  Path to INI file with [API] base_url. If set, the
                         default for --api-url is read from this file.
  HPCPERF_API_KEY_URL     Override the URL shown when authentication fails
                         (where to obtain an API key).

Files:
  %s  Cached API keys per API base URL. Written when you pass --api-key.
""" % API_KEY_CACHE,
  )
  parser.add_argument(
      "--api-url",
      default=get_api_base_url() or "http://localhost:8000/api/",
      help="Base URL for the HPCPerfStats REST API (default: from INI or %(default)s)",
  )
  parser.add_argument(
      "--api-key",
      help=(
          "API key for authenticating to the HPCPerfStats REST API. "
          "If omitted, a cached key in %s is used when present."
      ) % API_KEY_CACHE,
  )
  parser.add_argument(
      "--insecure",
      action="store_true",
      help="Disable TLS certificate verification for HTTPS requests.",
  )
  parser.add_argument("jid", help="Job id to summarize")
  args = parser.parse_args(argv)
  verify_tls = not args.insecure
  # Determine API key (CLI or cache) and cache if provided explicitly.
  api_key = args.api_key
  if api_key:
    save_cached_api_key(args.api_url, api_key)
  else:
    api_key = load_cached_api_key(args.api_url)
  if not api_key:
    help_url = api_key_help_url(args.api_url)
    print(
        "No API key found. Create one at this browsable page:\n  %s\n"
        "Then run this command again with --api-key (it will be cached for future use)."
        % help_url,
        file=sys.stderr,
    )
    return 1
  return print_jobstats(args.jid, args.api_url, verify_tls, api_key)


if __name__ == "__main__":
  raise SystemExit(main())
