#!/usr/bin/env python
"""Text CLI summary for a single Slurm job.

This script prints a job-efficiency style report similar to the
Princeton `jobstats` tool, using the same DB and metrics that power the
HPCPerfStats web UI.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

BAR_WIDTH = 60
API_KEY_CACHE = Path.home() / ".hpcperfstats-api"


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


def _api_key_help_url(api_url: str) -> str:
  """Best-effort URL where the user can obtain an API key.

  Prefer env override; otherwise strip /api/ and point to login_prompt.
  """
  override = os.environ.get("HPCPERF_API_KEY_URL")
  if override:
    return override
  root = api_url
  if "/api/" in root:
    root = root.split("/api/", 1)[0]
  root = root.rstrip("/")
  return root + "/login_prompt"


def _load_cached_api_key(api_url: str) -> Optional[str]:
  """Load API key for api_url from ~/.hpcperfstats-api if present.

  Supported formats:
  - Single line file with just the key (applies to all URLs)
  - One mapping per line: '<base_url> <key>'
  Lines starting with '#' are ignored.
  """
  if not API_KEY_CACHE.exists():
    return None
  try:
    text = API_KEY_CACHE.read_text(encoding="utf-8")
  except OSError:
    return None
  lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
  if not lines:
    return None
  # Single-key mode
  if len(lines) == 1 and " " not in lines[0]:
    return lines[0]
  base = api_url.rstrip("/")
  for line in lines:
    if line.startswith("#"):
      continue
    parts = line.split(None, 1)
    if len(parts) != 2:
      continue
    url, key = parts
    if url.rstrip("/") == base:
      return key
  return None


def _save_cached_api_key(api_url: str, api_key: str) -> None:
  """Persist API key for api_url into ~/.hpcperfstats-api."""
  base = api_url.rstrip("/")
  lines = []
  if API_KEY_CACHE.exists():
    try:
      existing = API_KEY_CACHE.read_text(encoding="utf-8")
      lines = existing.splitlines()
    except OSError:
      lines = []
  # Remove any previous mapping for this URL and any legacy single-key lines
  new_lines = []
  for line in lines:
    if not line.strip():
      continue
    if line.lstrip().startswith("#"):
      new_lines.append(line)
      continue
    parts = line.split(None, 1)
    # Drop legacy single-key lines (no URL) or old mapping for this base URL
    if len(parts) == 1:
      continue
    if len(parts) == 2 and parts[0].rstrip("/") == base:
      continue
    new_lines.append(line)
  new_lines.append(f"{base} {api_key}")
  try:
    API_KEY_CACHE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
  except OSError:
    # Failing to cache should not break the CLI.
    pass


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


def _get_json(session: requests.Session,
              base_url: str,
              path: str,
              verify: bool,
              api_key: Optional[str]) -> Tuple[Optional[Dict[str, object]], int]:
  url = base_url.rstrip("/") + "/" + path.lstrip("/")
  headers = {}
  if api_key:
    headers["Authorization"] = f"Api-Key {api_key}"
  try:
    resp = session.get(url, timeout=30, verify=verify, headers=headers)
  except requests.RequestException as exc:
    print(f"Failed to contact API at {url}: {exc}")
    return None, 0
  if resp.status_code == 404:
    return None, resp.status_code
  if resp.status_code in (401, 403):
    help_url = _api_key_help_url(base_url)
    print(
        "Authentication with the HPCPerfStats API failed "
        f"({resp.status_code})."
    )
    print(
        "Obtain an API key from:\n"
        f"  {help_url}\n"
        "Then run this command again with --api-key or HPCPERF_API_KEY.\n"
        f"The key will be cached in {API_KEY_CACHE}."
    )
    return None, resp.status_code
  if not resp.ok:
    print(f"API request failed ({resp.status_code}) for {url}: {resp.text}")
    return None, resp.status_code
  try:
    return resp.json(), resp.status_code
  except ValueError:
    print(f"API returned invalid JSON for {url}")
    return None, resp.status_code


def print_jobstats(jid: str,
                   api_url: str,
                   verify_tls: bool,
                   api_key: Optional[str]) -> int:
  """Fetch job + metrics via REST API and print a jobstats-style summary."""
  session = requests.Session()

  detail, status = _get_json(
      session, api_url, f"jobs/{jid}/", verify_tls, api_key
  )
  if detail is None:
    if status == 0:
      return 1
    # Auth and connectivity were already handled in _get_json.
    print(f"No job found with id {jid}")
    return 1

  # At this point authentication to the API succeeded; persist URL->key mapping.
  if api_key:
    _save_cached_api_key(api_url, api_key)

  home, _ = _get_json(session, api_url, "home/", verify_tls, api_key)
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
      print(f"  {metric.get('metric', ''):20s} {value!r} {units}")

  return 0


def main(argv: Optional[list[str]] = None) -> int:
  parser = argparse.ArgumentParser(
      description="Print an efficiency summary for a single Slurm job.",
  )
  parser.add_argument(
      "--api-url",
      default=os.environ.get("HPCPERF_API_URL", "http://localhost:8000/api/"),
      help="Base URL for the HPCPerfStats REST API (default: %(default)s)",
  )
  parser.add_argument(
      "--api-key",
      help=(
          "API key for authenticating to the HPCPerfStats REST API. "
          "If omitted, HPCPERF_API_KEY or a cached key in "
          f"{API_KEY_CACHE} will be used when present."
      ),
  )
  parser.add_argument(
      "--insecure",
      action="store_true",
      help="Disable TLS certificate verification for HTTPS requests.",
  )
  parser.add_argument("jid", help="Job id to summarize")
  args = parser.parse_args(argv)
  verify_tls = not args.insecure
  # Determine API key (CLI > env > cache) and cache if provided explicitly.
  api_key = args.api_key or os.environ.get("HPCPERF_API_KEY")
  if api_key:
    _save_cached_api_key(args.api_url, api_key)
  else:
    api_key = _load_cached_api_key(args.api_url)
  return print_jobstats(args.jid, args.api_url, verify_tls, api_key)


if __name__ == "__main__":
  raise SystemExit(main())

