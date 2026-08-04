"""Run sacct for a date range and either POST to the API or write daily .txt files.

Two mutually exclusive modes:

- **API mode** (default): each day's pipe-delimited sacct output is POSTed to
  ``sacct/ingest/``, which uses sync_acct logic and also writes
  ``{acct_path}/YYYY-MM-DD.txt`` on the server.
- **File mode** (``-f DIR``): write the same pipe-delimited body to
  ``DIR/YYYY-MM-DD.txt`` locally (same naming as the ingest API / sync_acct).
  DIR must already exist. Do not combine with ``--api-key``.

API mode requires ``[API] base_url`` in the INI pointed to by
HPCPERFSTATS_TOOLS_INI, plus ``--api-key`` or a key cached in
``~/.hpcperfstats-api`` (same scheme as jobstats_cli).
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Optional

from dateutil.parser import parse

from .api_client import ApiClient
from .api_key_cache import (
    API_KEY_CACHE,
    api_key_help_url,
    load_cached_api_key,
    save_cached_api_key,
)
from .config import get_api_base_url


def _daterange(start_date: datetime, end_date: datetime, inclusive_end: bool = False):
    """Yield each date from start_date through end_date, one day at a time."""
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


def run_sacct_for_date(single_date):
    """Run sacct for a single day; return (date_str, stdout_bytes) or (date_str, None) on failure."""
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


def write_accounting_daily_file(outdir: str, date_str: str, body: str) -> str:
    """Write pipe-delimited sacct body to ``{outdir}/{date_str}.txt``.

    Matches the naming used by the sacct ingest API / ``persist_accounting_daily_file``
    (``YYYY-MM-DD.txt``). Uses a temp file + ``os.replace`` for an atomic overwrite.
    Returns the final path written.
    """
    path = os.path.join(outdir, "%s.txt" % date_str)
    tmp_path = "%s.tmp" % path
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp_path, path)
    return path


def send_to_api(base_url, api_key, date_str, body):
    """POST sacct output to the ingest endpoint. Return (success, message).

    Some deployments may issue an HTTP redirect (for example, HTTP→HTTPS or
    path normalization). The Python requests library may convert a POST into a
    GET when following a 301/302 redirect, which would cause Django to return
    "405 Method Not Allowed" on the ingest view (which only allows POST).

    To avoid this, we first send the request with redirects disabled and, if
    we receive a redirect status with a Location header, we re‑POST once to
    the redirected URL while preserving the HTTP method and body.
    """
    client = ApiClient(base_url=base_url, api_key=api_key, verify_tls=True, timeout=300)
    result = client.post_text(f"sacct/ingest/?date={date_str}", body=body, timeout=300)
    if not result.ok:
        return False, result.error or "request failed"
    if not isinstance(result.data, dict):
        return False, "Invalid JSON: expected object payload"
    return True, result.data.get("inserted", 0)


def _parse_date_range(args):
    try:
        start_date = parse(args.start_date) if args.start_date else datetime.now()
    except Exception:
        start_date = datetime.now()

    try:
        end_date = parse(args.end_date) if args.end_date else start_date + timedelta(1)
    except Exception:
        end_date = start_date + timedelta(1)
    return start_date, end_date


def _run_file_mode(file_dir: str, start_date: datetime, end_date: datetime) -> None:
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
        path = write_accounting_daily_file(file_dir, date_str, body)
        print(f"{date_str}: wrote {path}")


def _run_api_mode(args, start_date: datetime, end_date: datetime) -> None:
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
