#!/usr/bin/env python3
"""
A/B gate for the columnar update_metrics path against the pandas baseline.

``run`` processes a fixed job set (metrics + detail/plot prewarm per job on
one thread, artifacts reset and cache flushed first, exactly as the Stage 0
profile did) and writes per-job wall times plus the process peak RSS. It is
tree-agnostic: run it from a git worktree at the golden corpus'
``pandas_path_commit`` for arm A and from the current tree (Codon kernels
loaded) for arm B, both inside ``tests/run_dev_container_pytest.sh --ft
--exec``. ``compare`` joins two arm reports into one A/B report and
``--check`` validates the newest A/B report: finished jobs per hour at least
``--min-ratio`` (1.25) times the pandas arm, jobs per minute not below the
pandas arm, peak RSS not above the pandas arm.

Attributes:
  DEFAULT_GLOB: A/B report files examined by ``--check``.
  DEFAULT_MIN_RATIO: Required jobs-per-hour ratio (columnar / pandas).
  GATE_TOKEN: Printed when ``--check`` passes.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import resource
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_GLOB = "test_runs/metrics_columnar_ab_*.json"
DEFAULT_MIN_RATIO = 1.25
GATE_TOKEN = "COLUMNAR_AB_GATE_OK"


def _peak_rss_kib() -> int:
  """
  Process peak resident set size so far.

  Returns:
    int: ``ru_maxrss`` in KiB (Linux reports KiB).

  Examples:
    >>> _peak_rss_kib() > 0
    True
  """
  return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def run_arm(
  jids: list[str], *, arm: str, out: Path, kernel_line: str | None
) -> dict[str, Any]:
  """
  Process ``jids`` sequentially and write the arm report.

  Args:
    jids (list[str]): Job ids in processing order.
    arm (str): Arm label (``pandas`` or ``columnar``).
    out (Path): Report path.
    kernel_line (str | None): Codon status line to record (columnar arm).

  Returns:
    dict[str, Any]: The arm report.

  Examples:
    >>> run_arm(
    ...   [], arm="x", out=Path("/tmp/a.json"), kernel_line=None
    ... )  # doctest: +SKIP
  """
  from django.core.cache import cache

  from hpcperfstats.analysis.metrics import update_metrics as um
  from hpcperfstats.analysis.metrics.lib import metrics
  from hpcperfstats.site.lib.machine.models import (
    job_detail_artifact,
    job_plot_artifact,
  )

  cache.clear()
  job_plot_artifact.objects.filter(jid_id__in=jids).delete()
  job_detail_artifact.objects.filter(jid_id__in=jids).delete()
  metrics_obj = metrics.Metrics()
  rss_before = _peak_rss_kib()
  jobs: dict[str, Any] = {}
  t_start = time.monotonic()
  for index, jid in enumerate(jids):
    ref = um._candidate_ref(jid)
    t0 = time.monotonic()
    outcome = metrics._unwrap_and_persist((metrics_obj, ref))
    metrics_wall = time.monotonic() - t0
    pre = um._prewarm_jid_on_metrics_pool(jid)
    wall = time.monotonic() - t0
    ok = str(outcome.get("status")) == "ok" and bool(pre.get("ok"))
    jobs[str(jid)] = {
      "wall_s": wall,
      "metrics_wall_s": metrics_wall,
      "prewarm_wall_s": wall - metrics_wall,
      "metrics_status": str(outcome.get("status")),
      "prewarm_ok": bool(pre.get("ok")),
      "ok": ok,
    }
    print(
      f"ab_arm={arm} [{index + 1}/{len(jids)}] jid={jid} wall_s={wall:.1f} "
      f"metrics={metrics_wall:.1f} ok={int(ok)}",
      flush=True,
    )
  total = time.monotonic() - t_start
  finished = sum(1 for row in jobs.values() if row["ok"])
  report = {
    "created": datetime.now(UTC).isoformat(),
    "arm": arm,
    "host": platform.node(),
    "python": sys.version.split()[0],
    "gil_enabled": bool(getattr(sys, "_is_gil_enabled", lambda: True)()),
    "codon_metrics": kernel_line,
    "jobs": jobs,
    "wall_s": total,
    "finished_jobs": finished,
    "jobs_per_hour": (finished / total * 3600.0) if total > 0 else 0.0,
    "jobs_per_min": (finished / total * 60.0) if total > 0 else 0.0,
    "peak_rss_kib": _peak_rss_kib(),
    "peak_rss_kib_before": rss_before,
  }
  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text(json.dumps(report, indent=2), encoding="utf-8")
  print(
    f"ab_arm={arm} finished={finished}/{len(jids)} wall_s={total:.1f} "
    f"jobs_per_hour={report['jobs_per_hour']:.1f} "
    f"peak_rss_kib={report['peak_rss_kib']} report={out}",
    flush=True,
  )
  return report


def compare_arms(
  pandas_path: Path, columnar_path: Path, out: Path
) -> dict[str, Any]:
  """
  Join two arm reports into one A/B report over their common jobs.

  Args:
    pandas_path (Path): Arm A report (pandas path).
    columnar_path (Path): Arm B report (columnar + Codon).
    out (Path): A/B report path.

  Returns:
    dict[str, Any]: The A/B report.

  Examples:
    >>> compare_arms(
    ...   Path("a.json"), Path("b.json"), Path("ab.json")
    ... )  # doctest: +SKIP
  """
  a = json.loads(pandas_path.read_text(encoding="utf-8"))
  b = json.loads(columnar_path.read_text(encoding="utf-8"))
  common = sorted(set(a["jobs"]) & set(b["jobs"]))
  ok_both = [j for j in common if a["jobs"][j]["ok"] and b["jobs"][j]["ok"]]
  wall_a = sum(float(a["jobs"][j]["wall_s"]) for j in ok_both)
  wall_b = sum(float(b["jobs"][j]["wall_s"]) for j in ok_both)
  n = len(ok_both)
  jph_a = (n / wall_a * 3600.0) if wall_a > 0 else 0.0
  jph_b = (n / wall_b * 3600.0) if wall_b > 0 else 0.0
  report = {
    "created": datetime.now(UTC).isoformat(),
    "pandas_report": str(pandas_path),
    "columnar_report": str(columnar_path),
    "common_jobs": common,
    "finished_both": n,
    "pandas_finished": int(a.get("finished_jobs", 0)),
    "columnar_finished": int(b.get("finished_jobs", 0)),
    "pandas_wall_s": wall_a,
    "columnar_wall_s": wall_b,
    "pandas_jobs_per_hour": jph_a,
    "columnar_jobs_per_hour": jph_b,
    "pandas_jobs_per_min": jph_a / 60.0,
    "columnar_jobs_per_min": jph_b / 60.0,
    "ratio_jobs_per_hour": (jph_b / jph_a) if jph_a > 0 else 0.0,
    "pandas_peak_rss_kib": int(a.get("peak_rss_kib", 0)),
    "columnar_peak_rss_kib": int(b.get("peak_rss_kib", 0)),
    "columnar_codon_metrics": b.get("codon_metrics"),
    "columnar_gil_enabled": b.get("gil_enabled"),
    "per_job": {
      j: {
        "pandas_wall_s": a["jobs"][j]["wall_s"],
        "columnar_wall_s": b["jobs"][j]["wall_s"],
        "speedup": (
          a["jobs"][j]["wall_s"] / b["jobs"][j]["wall_s"]
          if b["jobs"][j]["wall_s"] > 0
          else 0.0
        ),
      }
      for j in ok_both
    },
  }
  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text(json.dumps(report, indent=2), encoding="utf-8")
  print(
    f"ab_compare jobs={n} ratio_jobs_per_hour={report['ratio_jobs_per_hour']:.3f} "
    f"pandas_jph={jph_a:.1f} columnar_jph={jph_b:.1f} "
    f"rss_kib pandas={report['pandas_peak_rss_kib']} "
    f"columnar={report['columnar_peak_rss_kib']} report={out}",
    flush=True,
  )
  return report


def check_reports(paths: Sequence[str], min_ratio: float) -> int:
  """
  Validate the newest A/B report against the Stage 9 gate.

  Args:
    paths (Sequence[str]): Candidate A/B reports (newest by mtime wins).
    min_ratio (float): Required columnar / pandas jobs-per-hour ratio.

  Returns:
    int: 0 when the gate passes, 1 otherwise.

  Examples:
    >>> check_reports([], 1.25)
    ab_check: no report files
    1
  """
  if not paths:
    print("ab_check: no report files", flush=True)
    return 1
  newest = max(paths, key=lambda p: os.path.getmtime(p))
  r = json.loads(Path(newest).read_text(encoding="utf-8"))
  problems = []
  if int(r.get("finished_both", 0)) < 1:
    problems.append("no job finished on both arms")
  if int(r.get("columnar_finished", 0)) < int(r.get("pandas_finished", 0)):
    problems.append("columnar arm finished fewer jobs than pandas")
  ratio = float(r.get("ratio_jobs_per_hour", 0.0))
  if ratio < min_ratio:
    problems.append(f"jobs/hour ratio {ratio:.3f} < {min_ratio}")
  if float(r.get("columnar_jobs_per_min", 0.0)) < float(
    r.get("pandas_jobs_per_min", 0.0)
  ):
    problems.append("jobs/min regressed")
  if int(r.get("columnar_peak_rss_kib", 0)) > int(
    r.get("pandas_peak_rss_kib", 0)
  ):
    problems.append("columnar peak RSS above pandas arm")
  if r.get("columnar_gil_enabled", True):
    problems.append("columnar arm measured with the GIL on")
  line = str(r.get("columnar_codon_metrics") or "")
  if "status=enabled" not in line:
    problems.append("columnar arm ran without the Codon kernels")
  for p in problems:
    print(f"ab_check FAIL: {p}", flush=True)
  if problems:
    return 1
  print(
    f"{GATE_TOKEN} ratio={ratio:.3f} jobs={r['finished_both']} report={newest}",
    flush=True,
  )
  return 0


def _select_jids(explicit: str | None, baseline: str | None) -> list[str]:
  """
  Job ids from ``--jids`` or from a baseline report's job keys.

  Args:
    explicit (str | None): Comma-separated ids.
    baseline (str | None): Report path whose ``jobs`` keys to reuse.

  Returns:
    list[str]: Job ids.

  Examples:
    >>> _select_jids("2,1", None)
    ['2', '1']
  """
  if explicit:
    return [j.strip() for j in explicit.split(",") if j.strip()]
  if baseline:
    data = json.loads(Path(baseline).read_text(encoding="utf-8"))
    return list(data.get("jobs", {}))
  return []


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry point (``run``, ``compare`` or ``--check``).

  Args:
    argv (Sequence[str] | None): Arguments without the program name.

  Returns:
    int: Process exit code.

  Examples:
    >>> main(["--check", "--glob", "/nonexistent/*.json"])
    ab_check: no report files
    1
  """
  parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  parser.add_argument(
    "command", nargs="?", choices=["run", "compare"], default=None
  )
  parser.add_argument("--arm", choices=["pandas", "columnar"])
  parser.add_argument("--jids", help="comma-separated job ids (run)")
  parser.add_argument("--baseline", help="report whose job ids to reuse (run)")
  parser.add_argument("--out", help="report path (run / compare)")
  parser.add_argument("--pandas", help="pandas arm report (compare)")
  parser.add_argument("--columnar", help="columnar arm report (compare)")
  parser.add_argument("--check", action="store_true")
  parser.add_argument("--glob", default=DEFAULT_GLOB)
  parser.add_argument("--min-ratio", type=float, default=DEFAULT_MIN_RATIO)
  args = parser.parse_args(argv)
  if args.check:
    return check_reports(sorted(glob.glob(args.glob)), args.min_ratio)
  stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
  if args.command == "compare":
    if not (args.pandas and args.columnar):
      parser.error("compare needs --pandas and --columnar")
    out = Path(args.out or f"test_runs/metrics_columnar_ab_{stamp}.json")
    compare_arms(Path(args.pandas), Path(args.columnar), out)
    return 0
  if args.command == "run":
    if not args.arm:
      parser.error("run needs --arm pandas|columnar")
    from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

    ensure_django()
    jids = _select_jids(args.jids, args.baseline)
    if not jids:
      print("run: no jobs selected", flush=True)
      return 1
    kernel_line = None
    if args.arm == "columnar":
      from hpcperfstats.analysis.metrics.lib.columnar import codon_bridge

      kernel_line = codon_bridge.format_startup_line()
      print(kernel_line, flush=True)
    out = Path(args.out or f"test_runs/metrics_ab_arm_{args.arm}_{stamp}.json")
    run_arm(jids, arm=args.arm, out=out, kernel_line=kernel_line)
    return 0
  parser.error("use 'run', 'compare' or --check")
  return 2


if __name__ == "__main__":
  sys.exit(main())
