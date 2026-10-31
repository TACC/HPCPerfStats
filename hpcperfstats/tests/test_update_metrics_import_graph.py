"""
Contract: the update_metrics path loads no pandas (plan Stage 10, gate G10).

Two checks: a fresh interpreter imports the scheduler, the metrics library,
the plot builders and the artifact persisters and ``pandas`` is absent from
``sys.modules``; and no module under the update_metrics path imports pandas
in its source.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PATH_MODULES = (
  "hpcperfstats.analysis.metrics.update_metrics",
  "hpcperfstats.analysis.metrics.lib.metrics",
  "hpcperfstats.analysis.metrics.lib.gen.jid_table",
  "hpcperfstats.analysis.metrics.lib.gen.utils",
  "hpcperfstats.analysis.metrics.lib.plot",
  "hpcperfstats.analysis.metrics.lib.plot.summaryplot",
  "hpcperfstats.analysis.metrics.lib.plot.roofline",
  "hpcperfstats.analysis.metrics.lib.plot.devplot",
  "hpcperfstats.analysis.metrics.lib.columnar.job_table",
  "hpcperfstats.analysis.metrics.lib.columnar.kernels",
  "hpcperfstats.analysis.metrics.lib.job_detail_fsio",
  "hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary",
  "hpcperfstats.site.lib.machine.job_detail_artifacts",
  "hpcperfstats.site.lib.machine.job_plot_artifacts",
)

PATH_SOURCE_DIRS = (
  REPO / "hpcperfstats" / "analysis" / "metrics",
  REPO / "hpcperfstats" / "dbload" / "lib" / "date_utils.py",
  REPO
  / "hpcperfstats"
  / "site"
  / "lib"
  / "machine"
  / "job_detail_artifacts.py",
  REPO / "hpcperfstats" / "site" / "lib" / "machine" / "job_plot_artifacts.py",
)

_IMPORT_RE = re.compile(r"^\s*(import pandas\b|from pandas\b)", re.M)


def _path_sources() -> list[Path]:
  out: list[Path] = []
  for entry in PATH_SOURCE_DIRS:
    if entry.is_file():
      out.append(entry)
      continue
    for path in entry.rglob("*.py"):
      if "/tests/" in str(path) or path.name.startswith("test_"):
        continue
      out.append(path)
  return out


def test_update_metrics_path_sources_do_not_import_pandas():
  offenders = [
    str(p.relative_to(REPO))
    for p in _path_sources()
    if _IMPORT_RE.search(p.read_text())
  ]
  assert offenders == [], (
    f"pandas imported on the update_metrics path: {offenders}"
  )


def test_update_metrics_import_does_not_load_pandas():
  code = (
    "import sys, django\n"
    "django.setup()\n"
    "import importlib\n"
    f"for m in {PATH_MODULES!r}:\n"
    "  importlib.import_module(m)\n"
    "print('PANDAS_LOADED' if 'pandas' in sys.modules else 'PANDAS_ABSENT')\n"
  )
  env = {
    "DJANGO_SETTINGS_MODULE": "hpcperfstats.site.hpcperfstats_site.settings",
    "PATH": "/usr/bin:/bin",
    "PYTHONPATH": str(REPO),
  }
  import os

  for key in ("HPCPERFSTATS_INI", "HOME", "LANG", "LC_ALL"):
    if key in os.environ:
      env[key] = os.environ[key]
  proc = subprocess.run(
    [sys.executable, "-c", code],
    capture_output=True,
    text=True,
    cwd=str(REPO),
    env=env,
    timeout=600,
    check=False,
  )
  assert proc.returncode == 0, proc.stderr[-2000:]
  assert "PANDAS_ABSENT" in proc.stdout, proc.stdout + proc.stderr[-2000:]
