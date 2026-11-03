"""scripts/collapse_percore_parallel.sh against a fake podman (no DB)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "collapse_percore_parallel.sh"

# Stands in for `podman exec` into the db and pipeline containers. SQL is
# answered by keyword; manage.py calls are recorded in calls.log.
_FAKE_PODMAN = r"""#!/bin/bash
ST=${FAKE_STATE:?}
[ "$1" = ps ] && { echo "hpcperfstats_db_1  Up"; exit 0; }
[ "$1" = exec ] || exit 2; shift
while [[ "$1" == -* ]]; do case "$1" in -e) shift 2;; *) shift;; esac; done
C=$1; shift
if [ "$C" = hpcperfstats_db_1 ]; then
  SQL=""; while [ $# -gt 0 ]; do [ "$1" = -c ] && SQL=$2; shift; done
  [ -z "$SQL" ] && SQL=$(cat)
  day=$(grep -o "20[0-9-]\{8\}" <<< "$SQL" | head -1)
  case "$SQL" in
    *pg_size_pretty*) printf '2026-10-01|t|_ti.c1|10 GB|1000\n2026-10-24|f|_ti.c2|200 GB|700\n';;
    *TABLESAMPLE*) echo 98.9;;
    *has_percore*) echo has_percore=true;;
    *"bool_or(is_compressed)"*)
      if [[ " ${FAKE_COMP:-} " == *" $day "* ]]; then echo t; else echo f; fi;;
    *pg_replication_slots*) echo "${FAKE_SLOTS:-0}";;
    *"SHOW archive_mode"*) echo "${FAKE_ARCH:-off}";;
    *pg_locks*) printf '%s\n' ${FAKE_MERGE_DAYS:-};;
    *"WITH RECURSIVE"*)
      n=5; [ "$day" = 2026-10-09 ] && n=0
      for i in $(seq 1 $n); do echo "c$i-$day.example"; done;;
    *reltuples*) case "$SQL" in *2026-10-25*) echo 350;; *) echo 120;; esac;;
    *"SELECT count(*) FROM pg_stat_activity"*) echo 0;;
    *) echo "(fake rows)";;
  esac
  exit 0
fi
case "$1" in
  pgrep) n=$(ls $ST/running.* 2>/dev/null | wc -l); echo $n; [ $n -gt 0 ];;
  pkill) echo "pkill $*" >> $ST/pkill.log;;
  python3)
    if [ "$2" = -c ]; then echo "${FAKE_FIX:-FIXED}"; exit 0; fi
    echo "$*" >> $ST/calls.log; m=$ST/running.$$; touch $m; sleep 0.2
    echo "TOTAL percore_rows=10 groups=2 kept_existing=0 new_rows=2"; rm -f $m;;
esac
"""


@pytest.fixture
def env(tmp_path):
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  fake = bin_dir / "podman"
  fake.write_text(_FAKE_PODMAN)
  fake.chmod(0o755)
  state = tmp_path / "state"
  state.mkdir()
  return {
    **os.environ,
    "PATH": f"{bin_dir}:{os.environ['PATH']}",
    "FAKE_STATE": str(state),
    "COLLAPSE_RUN_DIR": str(tmp_path / "run"),
    "COLLAPSE_SHARD_ROWS": "100",
    "COLLAPSE_PARALLEL": "3",
  }


def _run(env, *args, **extra):
  return subprocess.run(
    [str(SCRIPT), *args],
    env={**env, **extra},
    capture_output=True,
    text=True,
    check=False,
    timeout=60,
  )


def _calls(env):
  path = Path(env["FAKE_STATE"]) / "calls.log"
  return path.read_text().splitlines() if path.exists() else []


def _wait_finished(env, n_jobs):
  logs = Path(env["COLLAPSE_RUN_DIR"]) / "current" / "logs"
  deadline = time.monotonic() + 30
  while time.monotonic() < deadline:
    done = [p for p in logs.glob("*.log") if "\nrc=" in p.read_text()]
    if len(done) == n_jobs:
      return
    time.sleep(0.2)
  raise AssertionError("jobs did not finish")


def test_script_is_executable_and_parses():
  assert SCRIPT.stat().st_mode & 0o111
  done = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True)
  assert done.returncode == 0, done.stderr


def test_start_runs_every_shard_once_with_a_day_and_its_hosts(env):
  """Regression: stdin of xargs was /dev/null, so one job ran with no args."""
  out = _run(env, "start", "2026-10-24", "2026-10-25", "2026-10-09")
  assert out.returncode == 0, out.stderr
  assert "2026-10-09 no hosts, skipped" in out.stdout
  _wait_finished(env, 6)  # 10-24: 2 shards, 10-25: 4 shards
  calls = _calls(env)
  assert len(calls) == 6
  for call in calls:
    assert "--apply" in call
    # Regression: system checks connected to an unreachable xalt MySQL host.
    assert "--skip-checks" in call
    assert "--since 2026-10-2" in call and "--host c" in call
  hosts = [w for c in calls for w in c.split() if w.endswith(".example")]
  assert len(hosts) == len(set(hosts)) == 10
  status = _run(env, "status")
  assert "finished=6" in status.stdout
  assert "percore_rows=60" in status.stdout


@pytest.mark.parametrize(
  ("extra", "message"),
  [
    ({"FAKE_FIX": "NOT_FIXED"}, "pipeline code state is NOT_FIXED"),
    ({"FAKE_COMP": "2026-10-24"}, "2026-10-24 is compressed"),
    ({"FAKE_MERGE_DAYS": "2026-10-24"}, "the merge is writing to 2026-10-24"),
    ({"FAKE_SLOTS": "1"}, "replication_slots=1"),
    ({"FAKE_ARCH": "on"}, "archive_mode=on"),
  ],
)
def test_start_refuses_unsafe_states_without_running_anything(
  env, extra, message
):
  out = _run(env, "start", "2026-10-24", **extra)
  assert out.returncode == 1
  assert message in out.stderr
  assert _calls(env) == []


@pytest.mark.parametrize("args", [["start"], ["start", "10/24"], ["bogus"]])
def test_bad_arguments_are_rejected(env, args):
  out = _run(env, *args)
  assert out.returncode == 1
  assert _calls(env) == []


def test_job_without_a_day_or_host_file_is_refused(env):
  Path(env["COLLAPSE_RUN_DIR"]).mkdir()
  for args in (["_job"], ["_job", "", ""], ["_job", "2026-10-25", "/nope"]):
    assert _run(env, *args).returncode == 0
  refused = (Path(env["COLLAPSE_RUN_DIR"]) / "refused.log").read_text()
  assert refused.count("REFUSED") == 3
  assert _calls(env) == []


def test_survey_reports_fix_state_and_per_day_share(env):
  out = _run(env, "survey", "2026-10-01")
  assert out.returncode == 0, out.stderr
  assert "pipeline code: FIXED" in out.stdout
  assert "2026-10-01|t|10 GB|1000|has_percore=true" in out.stdout
  assert "2026-10-24|f|200 GB|700|98.9" in out.stdout
