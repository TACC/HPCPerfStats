"""Drift guard: deprecated flat module paths must not return after colocation."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG = REPO_ROOT / "hpcperfstats"

# Entry scripts and migration tooling allowed at legacy flat paths.
ALLOWLIST_FILES = {
    PKG / "dbload/sync_timedb.py",
    PKG / "dbload/sync_acct.py",
    PKG / "dbload/sync_timedb_archive.py",
    PKG / "listend.py",
    PKG / "listend_drain.py",
    PKG / "seal_syslog_daily.py",
    PKG / "render_syslog_ng_generated.py",
    PKG / "analysis/metrics/update_metrics.py",
    PKG / "site/manage.py",
    REPO_ROOT / "test_runs/migrate_lib_colocation.py",
}

DEPRECATED_ROOT_MODULES = [
    "conf_parser.py",
    "print_utils.py",
    "shutdown_utils.py",
    "file_locking.py",
    "process_title.py",
    "process_memory.py",
    "django_bootstrap.py",
    "dbwait.py",
    "rediswait.py",
    "ini_section_placement.py",
]

DEPRECATED_DBLOAD_LIBS = [
    "sync_timedb_parsing.py",
    "date_utils.py",
    "io_helpers.py",
]


def test_deprecated_root_modules_removed():
  for name in DEPRECATED_ROOT_MODULES:
    path = PKG / name
    assert not path.is_file(), f"deprecated flat module still present: {path}"


def test_deprecated_dbload_siblings_removed():
  for name in DEPRECATED_DBLOAD_LIBS:
    path = PKG / "dbload" / name
    assert not path.is_file(), f"deprecated dbload sibling still present: {path}"


def test_monitor_naming_not_at_package_root():
  assert not (PKG / "monitor_naming").is_dir()


def test_analysis_plot_not_at_legacy_path():
  assert not (PKG / "analysis/plot").is_dir()
  assert not (PKG / "analysis/gen").is_dir()


def test_machine_app_under_site_lib():
  assert (PKG / "site/lib/machine/models.py").is_file()
  assert not (PKG / "site/machine/models.py").is_file()


def test_package_lib_paths_not_gitignored():
  """Root .gitignore had lib/ which hid colocated trees from git (delete-only commits)."""
  import subprocess

  samples = [
      "hpcperfstats/site/lib/machine/migrations/0001_initial.py",
      "hpcperfstats/dbload/lib/conf_parser.py",
      "hpcperfstats/analysis/metrics/lib/metrics.py",
      "hpcperfstats/lib/monitor_identity.py",
  ]
  for rel in samples:
    proc = subprocess.run(
        ["git", "check-ignore", "-v", rel],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, (
        f"{rel} must not be gitignored; check-ignore: {proc.stdout.strip()}"
    )


def test_pycache_under_package_lib_is_gitignored():
  import subprocess

  samples = [
      "hpcperfstats/dbload/lib/__pycache__/shutdown_utils.cpython-312.pyc",
      "hpcperfstats/site/lib/machine/__pycache__/models.cpython-312.pyc",
  ]
  for rel in samples:
    proc = subprocess.run(
        ["git", "check-ignore", "-v", rel],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"{rel} must be gitignored"
    assert "__pycache__" in proc.stdout or ".pyc" in proc.stdout


def test_metrics_tests_live_under_tests_package_only():
  """Metrics pytest modules must not be colocated beside lib/ or update_metrics.py."""
  metrics_root = REPO_ROOT / "hpcperfstats" / "analysis" / "metrics"
  stray = [
      p.relative_to(metrics_root)
      for p in metrics_root.rglob("test_*.py")
      if not (len(p.relative_to(metrics_root).parts) >= 2 and p.relative_to(metrics_root).parts[0] == "tests")
  ]
  assert stray == [], f"move metrics tests under analysis/metrics/tests/: {stray}"
  tests_dir = metrics_root / "tests"
  assert tests_dir.is_dir()
  assert any(tests_dir.glob("test_*.py")), "metrics/tests/ must contain pytest modules"
