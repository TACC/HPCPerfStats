"""Smoke tests for scripts/apply_compose_cpu_pinning.py."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CPU_SCRIPT = _REPO / "scripts" / "apply_compose_cpu_pinning.py"


@pytest.fixture
def numa_sysfs(tmp_path):
  root = tmp_path / "sys" / "devices" / "system" / "node"
  for nid, cl in ((0, "0-15"), (1, "16-31")):
    d = root / f"node{nid}"
    d.mkdir(parents=True)
    (d / "cpulist").write_text(cl, encoding="utf-8")
  return root


@pytest.fixture
def minimal_ini(tmp_path):
  p = tmp_path / "t.ini"
  p.write_text(
      "[DEFAULT]\n"
      "machine = test\n"
      "server = test\n"
      "data_dir = /tmp\n"
      "staff_email_domain = local\n"
      "timezone = UTC\n"
      "total_cores = 64\n"
      "web_numa_node = 0\n"
      "pipeline_numa_node = 1\n"
      "debug = no\n"
      "[PORTAL]\n"
      "dbname = test\n"
      "username = u\n"
      "password = p\n"
      "port = 5432\n"
      "host = localhost\n"
      "archive_dir = /tmp\n"
      "acct_path = /tmp\n"
      "daily_archive_dir = /tmp\n"
      "engine_name = django.db.backends.postgresql\n"
      "[RMQ]\n"
      "rmq_server = localhost\n"
      "rmq_queue = test\n"
      "[XALT]\n"
      "xalt_engine = django.db.backends.sqlite3\n"
      "xalt_name = xalt\n"
      "xalt_user = u\n"
      "xalt_password = p\n"
      "xalt_host = localhost\n"
      "[OAUTH2]\n"
      "client_id = id\n"
      "client_key = key\n"
      "authorize_url = http://localhost\n"
      "oauth_base_url = http://localhost\n",
      encoding="utf-8",
  )
  return p


def test_apply_compose_cpu_pinning_dry_run_numa(numa_sysfs, minimal_ini):
  proc = subprocess.run(
      [
          sys.executable,
          str(_CPU_SCRIPT),
          "--sysfs",
          str(numa_sysfs),
          "--dry-run",
      ],
      cwd=str(_REPO),
      capture_output=True,
      text=True,
      check=False,
      env={**os.environ, "HPCPERFSTATS_INI": str(minimal_ini), "PYTHONPATH": str(_REPO)},
  )
  assert proc.returncode == 0, proc.stderr
  assert "---\n" in proc.stdout
  assert "  db:" in proc.stdout
  assert "  web:" in proc.stdout
  assert "cpuset: \"0-15\"" in proc.stdout
  assert "cpuset: \"16-31\"" in proc.stdout


@pytest.fixture
def numa_sysfs_single_node(tmp_path):
  root = tmp_path / "sys" / "devices" / "system" / "node"
  d = root / "node0"
  d.mkdir(parents=True)
  (d / "cpulist").write_text("0-39", encoding="utf-8")
  return root


@pytest.fixture
def minimal_ini_single_node(tmp_path):
  p = tmp_path / "one.ini"
  p.write_text(
      "[DEFAULT]\n"
      "machine = test\n"
      "server = test\n"
      "data_dir = /tmp\n"
      "staff_email_domain = local\n"
      "timezone = UTC\n"
      "total_cores = 40\n"
      "web_numa_node = 0\n"
      "pipeline_numa_node = 0\n"
      "debug = no\n"
      "[PORTAL]\n"
      "dbname = test\n"
      "username = u\n"
      "password = p\n"
      "port = 5432\n"
      "host = localhost\n"
      "archive_dir = /tmp\n"
      "acct_path = /tmp\n"
      "daily_archive_dir = /tmp\n"
      "engine_name = django.db.backends.postgresql\n"
      "[RMQ]\n"
      "rmq_server = localhost\n"
      "rmq_queue = test\n"
      "[XALT]\n"
      "xalt_engine = django.db.backends.sqlite3\n"
      "xalt_name = xalt\n"
      "xalt_user = u\n"
      "xalt_password = p\n"
      "xalt_host = localhost\n"
      "[OAUTH2]\n"
      "client_id = id\n"
      "client_key = key\n"
      "authorize_url = http://localhost\n"
      "oauth_base_url = http://localhost\n",
      encoding="utf-8",
  )
  return p


def test_apply_compose_cpu_pinning_dry_run_single_node(
    numa_sysfs_single_node,
    minimal_ini_single_node,
):
  proc = subprocess.run(
      [
          sys.executable,
          str(_CPU_SCRIPT),
          "--sysfs",
          str(numa_sysfs_single_node),
          "--dry-run",
      ],
      cwd=str(_REPO),
      capture_output=True,
      text=True,
      check=False,
      env={
          **os.environ,
          "HPCPERFSTATS_INI": str(minimal_ini_single_node),
          "PYTHONPATH": str(_REPO),
      },
  )
  assert proc.returncode == 0, proc.stderr
  assert proc.stdout.count("0-39") >= 2
  assert "  web:" in proc.stdout
  assert "  pipeline:" in proc.stdout


def test_apply_compose_cpu_pinning_inactive_writes_empty_blocks(tmp_path, minimal_ini):
  infra = tmp_path / "infra.yaml"
  app = tmp_path / "app.yaml"
  proc = subprocess.run(
      [
          sys.executable,
          str(_CPU_SCRIPT),
          "--inactive",
          "--infra-out",
          str(infra),
          "--app-out",
          str(app),
      ],
      cwd=str(_REPO),
      capture_output=True,
      text=True,
      check=False,
      env={**os.environ, "HPCPERFSTATS_INI": str(minimal_ini), "PYTHONPATH": str(_REPO)},
  )
  assert proc.returncode == 0, proc.stderr
  assert "services: {}" in infra.read_text()
  assert "services: {}" in app.read_text()
