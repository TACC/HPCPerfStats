#!/usr/bin/env python3
"""Plan-driven cpuset budget helper for pipeline overlap benchmarking.

Prints process accounting buckets, derived S/A/M/R caps, and a reduced
S/M tuning matrix. Optionally executes the workflow command for each profile.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(_REPO_ROOT))

import hpcperfstats.dbload.lib.conf_parser as cfg


def _profiles_from_budget():
  budget = cfg.derive_pipeline_cpuset_priority_budget()
  s = budget["sync_ingest_cap"]
  a = budget["sync_archive_cap"]
  m = budget["metrics_cap"]
  c = budget["effective_cores"]
  profiles = [
      {
          "name": "derived",
          "sync_pool_process_cap": s,
          "sync_archive_pool_process_cap": a,
          "metrics_pool_process_cap": m,
          "env_overrides": {
              "HPCPERFSTATS_PIPELINE_OVERLAP_MODE": "balanced",
              "SYNC_ENABLE_OVERPROVISION_MODE": "0",
          },
      },
      {
          "name": "tune_s_minus_1_m_plus_1",
          "sync_pool_process_cap": max(1, s - 1),
          "sync_archive_pool_process_cap": a,
          "metrics_pool_process_cap": min(c, m + 1),
          "env_overrides": {
              "HPCPERFSTATS_PIPELINE_OVERLAP_MODE": "balanced",
              "SYNC_ENABLE_OVERPROVISION_MODE": "0",
          },
      },
      {
          "name": "tune_s_plus_1_m_minus_1",
          "sync_pool_process_cap": min(c, s + 1),
          "sync_archive_pool_process_cap": a,
          "metrics_pool_process_cap": max(1, m - 1),
          "env_overrides": {
              "HPCPERFSTATS_PIPELINE_OVERLAP_MODE": "balanced",
              "SYNC_ENABLE_OVERPROVISION_MODE": "0",
          },
      },
      {
          "name": "ingest_priority",
          "sync_pool_process_cap": s,
          "sync_archive_pool_process_cap": a,
          "metrics_pool_process_cap": m,
          "env_overrides": {
              "HPCPERFSTATS_PIPELINE_OVERLAP_MODE": "ingest_priority",
              "SYNC_ENABLE_OVERPROVISION_MODE": "0",
          },
      },
      {
          "name": "sync_overprovision",
          "sync_pool_process_cap": min(c + max(1, c // 2), max(1, int(c * 2))),
          "sync_archive_pool_process_cap": min(c, max(1, a + 1)),
          "metrics_pool_process_cap": max(1, m),
          "env_overrides": {
              "HPCPERFSTATS_PIPELINE_OVERLAP_MODE": "ingest_priority",
              "SYNC_ENABLE_OVERPROVISION_MODE": "1",
              "SYNC_BUDGET_OVERCOMMIT_FACTOR": "1.25",
              "SYNC_OVERPROVISION_INGEST_MULTIPLIER": "1.20",
              "SYNC_OVERPROVISION_ARCHIVE_MULTIPLIER": "1.00",
              "SYNC_OVERPROVISION_METRICS_MULTIPLIER": "0.85",
          },
      },
  ]
  return budget, profiles


def _run_profile(profile, skip_build):
  name = profile["name"]
  s = profile["sync_pool_process_cap"]
  a = profile["sync_archive_pool_process_cap"]
  m = profile["metrics_pool_process_cap"]
  env = dict(os.environ)
  env["SYNC_POOL_PROCESS_CAP"] = str(s)
  env["SYNC_ARCHIVE_POOL_PROCESS_CAP"] = str(a)
  env["METRICS_POOL_PROCESS_CAP"] = str(m)
  for key, value in profile.get("env_overrides", {}).items():
    env[str(key)] = str(value)
  cmd = ["tests/run_pipeline_e2e_workflow.sh"]
  if skip_build:
    cmd.append("--skip-build")
  t0 = time.time()
  proc = subprocess.run(cmd, env=env, check=False)
  return {
      "name": name,
      "sync_pool_process_cap": s,
      "sync_archive_pool_process_cap": a,
      "metrics_pool_process_cap": m,
      "env_overrides": profile.get("env_overrides", {}),
      "exit_code": proc.returncode,
      "elapsed_seconds": round(time.time() - t0, 2),
  }


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--run",
      action="store_true",
      help="Execute workflow command for each profile.",
  )
  parser.add_argument(
      "--skip-build",
      action="store_true",
      help="Pass --skip-build to workflow runner during --run.",
  )
  args = parser.parse_args()

  budget, profiles = _profiles_from_budget()
  buckets = cfg.pipeline_cpu_process_buckets(include_browser_phase=True, include_rsync=True)
  report = {
      "effective_cores": budget["effective_cores"],
      "derived_budget": budget,
      "buckets": buckets,
      "profiles": [
          profile
          for profile in profiles
      ],
  }
  print(json.dumps(report, indent=2, sort_keys=True))
  if not args.run:
    return 0

  results = []
  for profile in profiles:
    print(
        "Running profile:",
        profile["name"],
        "S/A/M=",
        profile["sync_pool_process_cap"],
        profile["sync_archive_pool_process_cap"],
        profile["metrics_pool_process_cap"],
        flush=True,
    )
    results.append(_run_profile(profile, skip_build=args.skip_build))
  print(json.dumps({"results": results}, indent=2, sort_keys=True))
  failures = [r for r in results if r["exit_code"] != 0]
  return 1 if failures else 0


if __name__ == "__main__":
  raise SystemExit(main())
