"""
Run sacct for a date range and either POST to the API or write daily .txt files.

Attributes:
  SACCT_FIELDS: Attribute.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Any, Iterator, Optional

from dateutil.parser import parse

from .api_client import ApiClient
from .api_key_cache import (
    API_KEY_CACHE,
    api_key_help_url,
    load_cached_api_key,
    save_cached_api_key,
)
from .config import get_api_base_url


def _daterange(
  start_date: datetime,
  end_date: datetime,
  inclusive_end: bool = False,
) -> Iterator[Any]:
    """
    Yield each date from start_date through end_date, one day at a time.
    
    Args:
      start_date (datetime): Start date.
      end_date (datetime): End date.
      inclusive_end (bool): Boolean flag for inclusive end.
    
    Yields:
      Iterator[Any]: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _daterange(None, None, True)  # doctest: +SKIP
    """
    days = int((end_date - start_date).days)
    if inclusive_end:
        days += 1
    for n in range(max(0, days)):
        yield start_date + timedelta(n)


SACCT_FIELDS = (
    "jobid,jobidraw,cluster,partition,qos,account,group,gid,user,uid,"
    "submit,eligible,start,end,elapsed,exitcode,state,nnodes,ncpus,reqcpus,"
    "reqmem,reqtres,alloctres,timelimit,nodelist,jobname"
)


def run_sacct_for_date(single_date: Any) -> Any:
    """
    Run sacct for a single day; return (date_str, stdout_bytes) or (date_str,.
    
      None) on failure.
    
    Args:
      single_date (Any): Single date passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> run_sacct_for_date(None)  # doctest: +SKIP
    """
    start_str = single_date.strftime("%Y-%m-%d")
    end_date = single_date + timedelta(1)
    end_str = end_date.strftime("%Y-%m-%d")
    cmd = [
        "/bin/sacct",
        "-a",
        "-s", "CANCELLED,COMPLETED,FAILED,NODE_FAIL,PREEMPTED,TIMEOUT,OUT_OF_MEMORY",
        "-P", "-X",
        "-S", start_str,
        "-E", end_str,
        "-o", SACCT_FIELDS,
    ]
    env = os.environ.copy()
    env["TZ"] = "UTC"
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=False,
        timeout=3600,
        env=env,
    )
    if result.returncode != 0:
        return start_str, None
    return start_str, result.stdout


def sacct_body_has_job_rows(body: str) -> bool:
    """
    True when sacct -P text has at least one job row after the header.
    
    Empty output or header-only (no jobs that day) returns False.
    
    Args:
      body (str): String for body.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> sacct_body_has_job_rows("x")  # doctest: +SKIP
    """
    lines = [ln for ln in body.splitlines() if ln.strip()]
    return len(lines) >= 2


def write_accounting_daily_file(outdir: str, date_str: str, body: str) -> str:
    """
    Write pipe-delimited sacct body to ``{outdir}/{date_str}.txt``.
    
    Matches the naming used by the sacct ingest API /
      ``persist_accounting_daily_file``
    (``YYYY-MM-DD.txt``). Uses a temp file + ``os.replace`` for an atomic
      overwrite.
    Returns the final path written.
    
    Args:
      outdir (str): String for outdir.
      date_str (str): String for date str.
      body (str): String for body.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> write_accounting_daily_file("x", "x", "x")  # doctest: +SKIP
    """
    path = os.path.join(outdir, "%s.txt" % date_str)
    tmp_path = "%s.tmp" % path
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp_path, path)
    return path


def send_to_api(base_url: Any, api_key: Any, date_str: Any, body: Any) -> Any:
    """
    POST sacct output to the ingest endpoint. Return (success, message).
    
    Some deployments may issue an HTTP redirect (for example, HTTP→HTTPS or
    path normalization). The Python requests library may convert a POST into a
    GET when following a 301/302 redirect, which would cause Django to return
    "405 Method Not Allowed" on the ingest view (which only allows POST).
    
    To avoid this, we first send the request with redirects disabled and, if
    we receive a redirect status with a Location header, we re‑POST once to
    the redirected URL while preserving the HTTP method and body.
    
    Args:
      base_url (Any): Base url passed to this helper.
      api_key (Any): Api key passed to this helper.
      date_str (Any): Date str passed to this helper.
      body (Any): Value to inspect (typically a numeric scalar).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> send_to_api(None, None, None, None)  # doctest: +SKIP
    """
    client = ApiClient(base_url=base_url, api_key=api_key, verify_tls=True, timeout=300)
    result = client.post_text(f"sacct/ingest/?date={date_str}", body=body, timeout=300)
    if not result.ok:
        return False, result.error or "request failed"
    if not isinstance(result.data, dict):
        return False, "Invalid JSON: expected object payload"
    return True, result.data.get("inserted", 0)


def _parse_date_range(args: Any) -> Any:
    """
    Internal helper to parse the date range.
    
    Args:
      args (Any): Args passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _parse_date_range(None)  # doctest: +SKIP
    """
    try:
        start_date = parse(args.start_date) if args.start_date else datetime.now()
    except Exception:
        start_date = datetime.now()

    try:
        end_date = parse(args.end_date) if args.end_date else start_date + timedelta(1)
    except Exception:
        end_date = start_date + timedelta(1)
    return start_date, end_date


def _run_file_mode(
  file_dir: str,
  start_date: datetime,
  end_date: datetime,
) -> None:
    """
    Internal helper to run the file mode.
    
    Args:
      file_dir (str): String for file dir.
      start_date (datetime): Start date.
      end_date (datetime): End date.
    
    Returns:
      None
    
    Examples:
      >>> _run_file_mode("x", None, None)  # doctest: +SKIP
    """
    if not os.path.isdir(file_dir):
        print(
            "Error: -f path is not a directory: %s" % file_dir,
            file=sys.stderr,
        )
        sys.exit(1)

    for single_date in _daterange(start_date, end_date):
        date_str, output = run_sacct_for_date(single_date)
        if output is None:
            print(f"Warning: sacct failed for {date_str}", file=sys.stderr)
            continue
        body = output.decode("utf-8", errors="replace")
        if not sacct_body_has_job_rows(body):
            print(f"{date_str}: no jobs; skipped")
            continue
        path = write_accounting_daily_file(file_dir, date_str, body)
        print(f"{date_str}: wrote {path}")


def _run_api_mode(args: Any, start_date: datetime, end_date: datetime) -> None:
    """
    Internal helper to run the api mode.
    
    Args:
      args (Any): Args passed to this helper.
      start_date (datetime): Start date.
      end_date (datetime): End date.
    
    Returns:
      None
    
    Examples:
      >>> _run_api_mode(None, None, None)  # doctest: +SKIP
    """
    base_url = get_api_base_url(default=None) or None
    if not base_url:
        print(
            "Error: API base URL not set. Set [API] base_url in "
            "HPCPERFSTATS_TOOLS_INI to point to your INI file with [API] base_url set.",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = args.api_key
    if api_key:
        save_cached_api_key(base_url, api_key)
    else:
        api_key = load_cached_api_key(base_url)
    if not api_key:
        help_url = api_key_help_url(base_url)
        print(
            "No API key found. Create one at this browsable page:\n  %s\n"
            "Then run this command again with --api-key (it will be cached for future use)."
            % help_url,
            file=sys.stderr,
        )
        sys.exit(1)

    for single_date in _daterange(start_date, end_date):
        date_str, output = run_sacct_for_date(single_date)
        if output is None:
            print(f"Warning: sacct failed for {date_str}", file=sys.stderr)
            continue
        body = output.decode("utf-8", errors="replace")
        ok, msg = send_to_api(base_url, api_key, date_str, body)
        if ok:
            print(f"{date_str}: ingested {msg} new job(s)")
        else:
            print(f"{date_str}: ingest failed: {msg}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> None:
    """
    Run this module's command-line entrypoint.
    
    Args:
      argv (Optional[list[str]]): Argv, or None when absent.
    
    Returns:
      None
    
    Examples:
      >>> main(None)  # doctest: +SKIP
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run sacct for a date range and either POST to the HPCPerfStats API "
            "or write daily YYYY-MM-DD.txt files (-f)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes (mutually exclusive):
  API mode (default)  POST each day's sacct output to sacct/ingest/.
                      Requires [API] base_url and --api-key (or a cached key).
  -f DIR              Write DIR/YYYY-MM-DD.txt locally (same format as the
                      ingest API / sync_acct). DIR must already exist.
                      Do not combine with --api-key.

Environment variables:
  HPCPERFSTATS_TOOLS_INI  Path to INI file with [API] base_url. Required for
                         API mode only.

Files:
  %s  Cached API keys per API base URL. Written when you pass --api-key.
""" % API_KEY_CACHE,
    )
    parser.add_argument(
        "start_date",
        nargs="?",
        default=None,
        help="Start date (YYYY-MM-DD or parseable). Default: today.",
    )
    parser.add_argument(
        "end_date",
        nargs="?",
        default=None,
        help="End date (exclusive). Default: start_date + 1 day.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "-f",
        metavar="DIR",
        dest="file_dir",
        help="Write daily YYYY-MM-DD.txt files to DIR instead of POSTing to the API. "
        "DIR must already exist. Mutually exclusive with --api-key.",
    )
    mode.add_argument(
        "--api-key",
        help="API key for authenticating to the HPCPerfStats REST API. "
        "If omitted in API mode, a cached key in %s is used when present. "
        "Mutually exclusive with -f." % API_KEY_CACHE,
    )
    args = parser.parse_args(argv)

    start_date, end_date = _parse_date_range(args)

    if args.file_dir is not None:
        _run_file_mode(args.file_dir, start_date, end_date)
    else:
        _run_api_mode(args, start_date, end_date)


if __name__ == "__main__":
    main()
