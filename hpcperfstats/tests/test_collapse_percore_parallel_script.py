"""scripts/collapse_percore_parallel.sh against a fake podman (no DB)."""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "collapse_percore_parallel.sh"

# Stands in for `podman exec` into the db and pipeline containers. In the db
# container the command runs as given (the script's sh -c wrapper included),
# with the container's POSTGRES_PASSWORD and the fake psql below on PATH.
# manage.py calls are recorded in calls.log.
_FAKE_PODMAN = r"""#!/bin/bash
ST=${FAKE_STATE:?}
[ "$1" = ps ] && { echo "hpcperfstats_db_1  Up"; exit 0; }
[ "$1" = cp ] && { echo "$2 $3" >> $ST/cp.log; [ -f "$2" ]; exit; }
[ "$1" = exec ] || exit 2; shift
while [[ "$1" == -* ]]; do case "$1" in -e) shift 2;; *) shift;; esac; done
C=$1; shift
if [ "$C" = hpcperfstats_db_1 ]; then
  POSTGRES_PASSWORD=${FAKE_DB_PASSWORD:-} PATH="$FAKE_DB_BIN:$PATH" exec "$@"
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

# psql in the db container. With FAKE_NEED_PW set the server wants that
# password: without -w psql sits at a prompt (the hpcperfstats04 hang), with a
# wrong one it fails. SQL is answered by keyword.
_FAKE_PSQL = r"""#!/bin/bash
ST=${FAKE_STATE:?}
if [ -n "${FAKE_NEED_PW:-}" ]; then
  case " $* " in *" -w "*) ;; *) echo "prompt" >> $ST/prompt.log; sleep 30; exit 1;; esac
  if [ "${PGPASSWORD:-}" != "$FAKE_NEED_PW" ]; then
    echo "psql: error: password authentication failed" >&2; exit 2
  fi
fi
{
  SQL=""; while [ $# -gt 0 ]; do [ "$1" = -c ] && SQL=$2; shift; done
  [ -z "$SQL" ] && SQL=$(cat)
  day=$(grep -o "20[0-9-]\{8\}" <<< "$SQL" | head -1)
  case "$SQL" in
    *pg_size_pretty*) printf '2026-10-01|t|_ti.c1|10 GB|1000\n2026-10-24|f|_ti.c2|200 GB|700\n';;
    *TABLESAMPLE*) echo 98.9;;
    *has_percore*) echo has_percore=true;;
    *"bool_or(is_compressed)"*)
      if [[ " ${FAKE_COMP:-} " == *" $day "* ]]; then echo t; else echo f; fi;;
    *"proc_name = 'policy_compression' LIMIT 1"*) echo "${FAKE_POLICY:-}";;
    *alter_job*) echo "$SQL" >> $ST/alter.log; echo done;;
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
}
"""


@pytest.fixture
def env(tmp_path):
  bin_dir = tmp_path / "bin"
  db_bin = tmp_path / "db_bin"
  for path, text in (
    (bin_dir / "podman", _FAKE_PODMAN),
    (db_bin / "psql", _FAKE_PSQL),
  ):
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
  state = tmp_path / "state"
  state.mkdir()
  return {
    **os.environ,
    "PATH": f"{bin_dir}:{os.environ['PATH']}",
    "FAKE_DB_BIN": str(db_bin),
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


_CMD = "site/lib/machine/management/commands/collapse_percore_host_data.py"


def test_compressed_days_are_repaired_in_place_not_refused(env):
  """The user wants compressed days repaired even if slow (hpcperfstats04)."""
  out = _run(env, "start", "2026-10-24", FAKE_COMP="2026-10-24")
  assert out.returncode == 0, out.stderr
  _wait_finished(env, 2)
  assert all("--since 2026-10-24" in c for c in _calls(env))
  assert len(_calls(env)) == 2


def test_start_pauses_an_enabled_policy_and_resume_policy_restores_it(env):
  alter = Path(env["FAKE_STATE"]) / "alter.log"
  out = _run(env, "start", "2026-10-24", FAKE_POLICY="1003 t")
  assert out.returncode == 0, out.stderr
  assert "paused compression policy job 1003" in out.stdout
  assert "alter_job(1003, scheduled => false)" in alter.read_text()
  _wait_finished(env, 2)
  out = _run(env, "resume-policy")
  assert "job 1003 is scheduled again" in out.stdout
  assert "alter_job(1003, scheduled => true)" in alter.read_text()
  assert "no compression policy was paused" in _run(env, "resume-policy").stdout


def test_start_leaves_a_paused_policy_alone(env):
  assert _run(env, "start", "2026-10-24", FAKE_POLICY="1003 f").returncode == 0
  assert not (Path(env["FAKE_STATE"]) / "alter.log").exists()


def _like_patterns() -> list[re.Pattern[str]]:
  """The LIKE prefixes in the script's REPAIR_SESSIONS, as regexes."""
  text = SCRIPT.read_text()
  block = text[text.index('REPAIR_SESSIONS="') : text.index("POLICY_NOTE=")]
  pats = re.findall(r"'((?:[^']|'')*%)'", block)
  return [
    re.compile(re.escape(p.replace("''", "'")).replace("%", ".*"), re.S)
    for p in pats
  ]


def test_stop_cancels_only_the_repair_commands_own_statements():
  """Regression: stop cancelled live update_metrics queries on hpcperfstats04
  because it matched any non-psql session touching host_data.
  """
  from unittest import mock

  from hpcperfstats.dbload.lib.host_data_percore_collapse import (
    _empty_output,
  )
  from hpcperfstats.site.lib.machine.management.commands import (
    collapse_percore_host_data as cmd_mod,
  )

  cursor = mock.MagicMock()
  cursor.fetchall.return_value = []
  conn = mock.MagicMock()
  conn.cursor.return_value.__enter__.return_value = cursor
  opts = {"lookback_minutes": 120}
  t0 = dt.datetime(2026, 10, 25, tzinfo=dt.UTC)
  t1 = t0 + dt.timedelta(hours=1)
  with (
    mock.patch.object(cmd_mod, "connection", conn),
    mock.patch.object(cmd_mod.transaction, "atomic"),
  ):
    cmd = cmd_mod.Command()
    cmd._migrate(t0, t1, ["h"], True, opts)
    cmd._percore_hosts(t0, t1, [])
    planned = _empty_output()
    planned["time"] = planned["time"].astype("datetime64[s, UTC]")
    cmd._verify(cursor, planned, ["h"], t0, t1, [])
  sqls = [c.args[0] for c in cursor.execute.call_args_list]
  # Each repair window lifts Timescale's DML decompression cap (compressed
  # days are repaired in place).
  assert cmd_mod._DML_DECOMPRESS_SQL in sqls
  sqls += [cmd_mod._INSERT_SQL, cmd_mod._DELETE_SQL, cmd_mod._DISCOVER_DAYS_SQL]
  statements = [s.replace("%%", "%") for s in sqls if "host_data" in s]
  pats = _like_patterns()
  for sql in statements:
    assert any(p.fullmatch(sql) for p in pats), sql
  others = [
    "COPY (SELECT h.time, h.host, h.type, h.event, h.dev FROM host_data h",
    "SELECT s.t, (SELECT 1 FROM host_data d WHERE d.host = 'c1')",
    "\nINSERT INTO host_data (\n  time, host, jid, type, dev, event, unit",
  ]
  for sql in others:
    assert not any(p.fullmatch(sql) for p in pats), sql
  assert "application_name <> 'psql' AND query ILIKE '%host_data%'" not in (
    SCRIPT.read_text()
  )


def test_start_copies_this_checkouts_repair_command_into_the_pipeline(env):
  """A fix to the command must reach the jobs with a git pull, no rebuild."""
  assert _run(env, "start", "2026-10-24").returncode == 0
  _wait_finished(env, 2)
  src, dst = (Path(env["FAKE_STATE"]) / "cp.log").read_text().split()
  assert Path(src).resolve() == REPO_ROOT / "hpcperfstats" / _CMD
  assert (
    dst == f"hpcperfstats_pipeline_1:/home/hpcperfstats/hpcperfstats/{_CMD}"
  )


def test_restart_reruns_the_same_days(env):
  assert _run(env, "start", "2026-10-24", "2026-10-25").returncode == 0
  _wait_finished(env, 6)
  first = sorted(_calls(env))
  out = _run(env, "restart")
  assert out.returncode == 0, out.stderr
  assert "restarting days: 2026-10-24 2026-10-25" in out.stdout
  _wait_finished(env, 6)
  assert sorted(_calls(env)) == sorted(first * 2)


def test_password_server_uses_the_container_password_and_never_prompts(env):
  """Regression: psql sat at a password prompt for 10 h on hpcperfstats04."""
  pw = {"FAKE_NEED_PW": "s3cret", "FAKE_DB_PASSWORD": "s3cret"}
  survey = _run(env, "survey", "2026-10-01", **pw)
  assert survey.returncode == 0, survey.stderr
  assert "2026-10-24|f|200 GB|700|98.9" in survey.stdout
  assert "2026-10-01|t|10 GB|1000|has_percore=true" in survey.stdout
  assert _run(env, "start", "2026-10-24", **pw).returncode == 0
  _wait_finished(env, 2)
  assert len(_calls(env)) == 2
  assert not (Path(env["FAKE_STATE"]) / "prompt.log").exists()


def test_wrong_password_fails_fast_instead_of_hanging(env):
  pw = {"FAKE_NEED_PW": "s3cret", "FAKE_DB_PASSWORD": "wrong"}
  started = time.monotonic()
  out = _run(env, "start", "2026-10-24", **pw)
  assert out.returncode == 1
  assert "psql failed" in out.stderr
  assert time.monotonic() - started < 20
  assert _calls(env) == []
  assert not (Path(env["FAKE_STATE"]) / "prompt.log").exists()


def test_survey_reports_fix_state_and_per_day_share(env):
  out = _run(env, "survey", "2026-10-01")
  assert out.returncode == 0, out.stderr
  assert "pipeline code: FIXED" in out.stdout
  assert "2026-10-01|t|10 GB|1000|has_percore=true" in out.stdout
  assert "2026-10-24|f|200 GB|700|98.9" in out.stdout
