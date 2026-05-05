#!/usr/bin/env python3
"""Load the bundled daemon sample into PostgreSQL for Job Detail local testing.

Rewrites the sample hostname to ``<short>.{DEFAULT.host_name_ext}``, ingests
stats via ``add_stats_file_to_db``, then inserts a synthetic sacct row via
``sync_acct_from_content``. Set ``SAMPLE_TEST_USERNAME`` to override the
accounting user column.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from hpcperfstats.django_bootstrap import ensure_django

ensure_django()

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.sync_acct import sync_acct_from_content
from hpcperfstats.dbload.sync_timedb import add_stats_file_to_db
from hpcperfstats.site.machine.models import job_data


def _first_timestamp_jid_host(text: str):
  for line in text.splitlines():
    s = line.strip()
    if s and s[0].isdigit():
      parts = s.split()
      if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
  return None, None, None


def main() -> int:
  repo_root = Path(__file__).resolve().parents[2]
  sample_path = (
      repo_root / "hpcperfstats" / "dbload" / "tests" / "HPCPerfStatsdDataSample"
  )
  if not sample_path.is_file():
    print("Sample not found:", sample_path, file=sys.stderr)
    return 1

  text = sample_path.read_text(encoding="utf-8", errors="replace")
  t_s, jid, old_host = _first_timestamp_jid_host(text)
  if not t_s or not jid or not old_host:
    print("Could not parse timestamp line from sample", file=sys.stderr)
    return 1

  short = old_host.split(".")[0]
  ext = cfg.get_host_name_ext().strip()
  if ext.startswith("."):
    new_host = short + ext
  else:
    new_host = "%s.%s" % (short, ext)

  text = text.replace(old_host, new_host)

  epoch = str(int(float(t_s)))
  stats_file = os.path.join(cfg.get_archive_dir_path(), new_host, epoch)
  lock = threading.Lock()
  add_stats_file_to_db(lock, stats_file, stats_file_contents=text)

  username = os.environ.get("SAMPLE_TEST_USERNAME", "sample_user")
  sacct = (
      "JobID|User|Account|Start|End|Submit|Partition|Timelimit|JobName|State|"
      "NNodes|ReqCPUS|NodeList\n"
      "%s|%s|acct|2024-06-01T00:00:00|2024-06-01T02:00:00|"
      "2024-06-01T00:00:00|normal|02:00:00|sample|COMPLETED|1|1|%s\n"
      % (jid, username, new_host)
  )
  jobs_in_db = set(str(x) for x in job_data.objects.values_list("jid", flat=True))
  sync_acct_from_content(sacct, jobs_in_db)
  print("Ingested sample for jid %s host %s" % (jid, new_host))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
