#!/usr/bin/env python3
"""
Stage 0 driver and gate for the update_metrics per-job phase profile.

``run`` (inside the dev container) executes the production worker tasks for a
job set — ``metrics._unwrap_and_persist`` then
``update_metrics._prewarm_jid_on_metrics_pool`` — with
``HPCPERFSTATS_METRICS_PHASE_PROFILE=1`` and writes a JSON report with the
per-job split across ``phase_profile.PHASES``. ``--check`` validates report
files and prints the aggregate replaceable fraction against the 40% go/no-go
gate of the columnar plan.

Attributes:
  DEFAULT_GATE_FRACTION: Replaceable share below which Codon cannot pay back.
  DEFAULT_GLOB: Report files examined by ``--check``.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import socket
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_GATE_FRACTION = 0.40
DEFAULT_GLOB = "test_runs/metrics_phase_profile_*.json"


def _select_jids(explicit: str | None, username: str | None) -> list[str]:
  """
  Resolve the job set: an explicit list, or every ``job_data`` row of a user.

  Args:
    explicit (str | None): Comma-separated jids, or None.
    username (str | None): ``job_data.username`` filter when no jids given.

  Returns:
    list[str]: Job ids in a stable order.

  Examples:
    >>> _select_jids("b,a", None)
    ['b', 'a']
  """
  if explicit:
    return [j.strip() for j in explicit.split(",") if j.strip()]
  from hpcperfstats.site.lib.machine.models import job_data

  qs = job_data.objects.all()
  if username:
    qs = qs.filter(username=username)
  return [
    str(j) for j in qs.order_by("start_time").values_list("jid", flat=True)
  ]


def _reset_artifacts(jids: list[str]) -> None:
  """
  Delete plot and detail artifacts so prewarm rebuilds everything.

  Args:
    jids (list[str]): Job ids to reset.

  Returns:
    None

  Examples:
    >>> _reset_artifacts([])  # doctest: +SKIP
  """
  from hpcperfstats.site.lib.machine.models import (
    job_detail_artifact,
    job_plot_artifact,
  )

  job_plot_artifact.objects.filter(jid_id__in=jids).delete()
  job_detail_artifact.objects.filter(jid_id__in=jids).delete()


def profile_jobs(
  jids: list[str],
  *,
  reset_artifacts: bool = True,
  flush_cache: bool = True,
) -> dict[str, Any]:
  """
  Run metrics + prewarm for each job with profiling on and collect the split.

  Args:
    jids (list[str]): Job ids to process sequentially on this thread.
    reset_artifacts (bool): Delete existing plot/detail artifacts first so
      prewarm builds instead of skipping on a matching fingerprint.
    flush_cache (bool): Clear the Django cache (Redis) so ``jid_table`` reads
      hit PostgreSQL instead of cached frames.

  Returns:
    dict[str, Any]: Report with ``jobs``, ``totals``, ``replaceable_fraction``.

  Examples:
    >>> profile_jobs([])  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics import update_metrics as um
  from hpcperfstats.analysis.metrics.lib import metrics, phase_profile
  from hpcperfstats.site.lib.machine.models import job_data

  os.environ[phase_profile.ENV_FLAG] = "1"
  if flush_cache:
    from django.core.cache import cache

    cache.clear()
  if reset_artifacts and jids:
    _reset_artifacts(jids)
  meta = {
    str(r["jid"]): r
    for r in job_data.objects.filter(jid__in=jids).values(
      "jid", "nhosts", "runtime", "start_time", "end_time"
    )
  }
  metrics_obj = metrics.Metrics()
  phase_profile.drain_records()
  jobs: dict[str, Any] = {}
  for index, jid in enumerate(jids):
    ref = um._candidate_ref(jid)
    t0 = time.monotonic()
    outcome = metrics._unwrap_and_persist((metrics_obj, ref))
    metrics_wall = time.monotonic() - t0
    pre = um._prewarm_jid_on_metrics_pool(jid)
    wall = time.monotonic() - t0
    merged = phase_profile.merge_records(phase_profile.drain_records())
    row = merged.get(str(jid)) or {"phases": {}, "wall_s": 0.0, "stages": []}
    info = meta.get(str(jid), {})
    row.update(
      {
        "wall_s": wall,
        "metrics_wall_s": metrics_wall,
        "prewarm_wall_s": wall - metrics_wall,
        "metrics_status": str(outcome.get("status")),
        "prewarm_ok": bool(pre.get("ok")),
        "nhosts": info.get("nhosts"),
        "runtime_s": info.get("runtime"),
        "replaceable_fraction": phase_profile.replaceable_fraction(
          {"wall_s": wall, "phases": row["phases"]}
        ),
      }
    )
    jobs[str(jid)] = row
    print(
      f"phase_profile: [{index + 1}/{len(jids)}] jid={jid} wall_s={wall:.1f} "
      f"metrics={metrics_wall:.1f} status={row['metrics_status']} "
      f"prewarm_ok={int(row['prewarm_ok'])} "
      + " ".join(
        f"{k}={float(v):.2f}" for k, v in sorted(row["phases"].items())
      ),
      flush=True,
    )
  return build_report(jobs)


def build_report(jobs: dict[str, Any]) -> dict[str, Any]:
  """
  Aggregate per-job rows into totals and the replaceable fraction.

  Args:
    jobs (dict[str, Any]): ``jid`` -> row with ``wall_s`` and ``phases``.

  Returns:
    dict[str, Any]: Report dict (``created``, ``host``, ``jobs``, ``totals``,
    ``wall_s``, ``replaceable_fraction``).

  Examples:
    >>> r = build_report({"1": {"wall_s": 10.0, "phases": {"db_fetch": 5.0}}})
    >>> r["replaceable_fraction"], r["totals"]["db_fetch"]
    (0.5, 5.0)
  """
  from hpcperfstats.analysis.metrics.lib.phase_profile import (
    PHASES,
    REPLACEABLE_PHASES,
  )

  totals = dict.fromkeys(PHASES, 0.0)
  wall = 0.0
  for row in jobs.values():
    wall += float(row.get("wall_s") or 0.0)
    for name, seconds in (row.get("phases") or {}).items():
      totals[name] = totals.get(name, 0.0) + float(seconds)
  replaceable = sum(totals.get(n, 0.0) for n in REPLACEABLE_PHASES)
  totals["other"] = max(0.0, wall - sum(totals.values()))
  return {
    "created": datetime.now(UTC).isoformat(),
    "host": socket.gethostname(),
    "python": platform.python_version(),
    "jobs": jobs,
    "totals": totals,
    "wall_s": wall,
    "replaceable_fraction": (replaceable / wall) if wall > 0 else 0.0,
  }


def check_reports(paths: list[str], gate: float) -> int:
  """
  Validate report files and print the gate marker when they are usable.

  Args:
    paths (list[str]): Report JSON files.
    gate (float): Replaceable fraction threshold for the go decision.

  Returns:
    int: 0 when every report has jobs and all phases; 1 otherwise.

  Examples:
    >>> check_reports([], 0.4)
    check: no report files found
    1
  """
  from hpcperfstats.analysis.metrics.lib.phase_profile import PHASES

  if not paths:
    print("check: no report files found")
    return 1
  problems: list[str] = []
  newest: dict[str, Any] | None = None
  for path in sorted(paths):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    jobs = report.get("jobs") or {}
    totals = report.get("totals") or {}
    missing = [p for p in PHASES if p not in totals]
    if not jobs:
      problems.append(f"{path}: no jobs")
    if missing:
      problems.append(f"{path}: missing phases {missing}")
    if float(report.get("wall_s") or 0.0) <= 0.0:
      problems.append(f"{path}: zero wall time")
    newest = report
  for problem in problems:
    print(f"check: {problem}")
  if problems or newest is None:
    return 1
  totals = newest["totals"]
  wall = float(newest["wall_s"])
  print(f"phase profile ({len(newest['jobs'])} jobs, wall_s={wall:.1f}):")
  for name in [*PHASES, "other"]:
    seconds = float(totals.get(name, 0.0))
    print(f"  {name:16s} {seconds:10.1f}s  {100.0 * seconds / wall:5.1f}%")
  fraction = float(newest["replaceable_fraction"])
  go = "yes" if fraction >= gate else "no"
  print(
    "METRICS_PHASE_PROFILE_GATE_OK "
    f"replaceable_fraction={fraction:.3f} gate={gate:.2f} go={go}"
  )
  return 0


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry point (``run`` writes a report; ``--check`` validates reports).

  Args:
    argv (Sequence[str] | None): Arguments without the program name.

  Returns:
    int: Process exit code.

  Examples:
    >>> main(["--check", "--glob", "/nonexistent/*.json"])
    check: no report files found
    1
  """
  parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  parser.add_argument("command", nargs="?", choices=["run"], default=None)
  parser.add_argument("--jids", help="comma-separated job ids")
  parser.add_argument("--username", default="corpus")
  parser.add_argument("--out", help="report JSON path (run)")
  parser.add_argument("--no-reset-artifacts", action="store_true")
  parser.add_argument("--no-flush-cache", action="store_true")
  parser.add_argument("--check", action="store_true")
  parser.add_argument("--glob", default=DEFAULT_GLOB)
  parser.add_argument("--gate", type=float, default=DEFAULT_GATE_FRACTION)
  args = parser.parse_args(argv)
  if args.check:
    return check_reports(sorted(glob.glob(args.glob)), float(args.gate))
  if args.command != "run":
    parser.error("use 'run' or --check")
  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

  ensure_django()
  jids = _select_jids(args.jids, args.username)
  if not jids:
    print("run: no jobs selected")
    return 1
  report = profile_jobs(
    jids,
    reset_artifacts=not args.no_reset_artifacts,
    flush_cache=not args.no_flush_cache,
  )
  out = Path(
    args.out
    or f"test_runs/metrics_phase_profile_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
  )
  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
  print(f"run: wrote {out}")
  return check_reports([str(out)], float(args.gate))


if __name__ == "__main__":
  sys.exit(main())
