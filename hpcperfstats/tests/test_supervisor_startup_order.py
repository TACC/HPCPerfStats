from __future__ import annotations

import configparser
import re
import subprocess
import textwrap
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _supervisord_conf_path() -> Path:
  return _repo_root() / "services-conf" / "supervisord.conf"


def _rsync_wrapper_path() -> Path:
  return _repo_root() / "services-conf" / "rsync_data_wrapper.sh"


def _guarded_rsync_paths() -> tuple[Path, Path]:
  root = _repo_root() / "services-conf"
  return root / "rsync_data.sh", root / "rsync_data.sh.example"


def test_supervisord_hpcperfstats_programs_set_home_and_user_environment():
  """Supervisord setuid does not update HOME; bash -lc must not inherit /root."""
  config = configparser.ConfigParser()
  config.read(_supervisord_conf_path())

  hpcperfstats_programs = [
    section
    for section in config.sections()
    if section.startswith("program:") and config.get(section, "user", fallback="") == "hpcperfstats"
  ]
  assert hpcperfstats_programs, "expected at least one hpcperfstats supervisord program"

  for section in hpcperfstats_programs:
    environment = config.get(section, "environment", fallback="")
    assert 'HOME="/home/hpcperfstats"' in environment, section
    assert 'USER="hpcperfstats"' in environment, section


def test_supervisord_python_pipeline_programs_stopwaitsecs_covers_drain():
  """stopwaitsecs must exceed sync_timedb SHUTDOWN_DRAIN_TIMEOUT_S (default supervisord is 10s)."""
  config = configparser.ConfigParser()
  config.read(_supervisord_conf_path())

  # Keep in lockstep with sync_timedb_queue_orchestrator.SHUTDOWN_DRAIN_TIMEOUT_S.
  min_stopwaitsecs = 120
  required_programs = (
    "program:hpcperfstats-rabbitmq-listener",
    "program:sync_timedb",
    "program:update_metrics",
  )
  for section in required_programs:
    assert config.has_section(section), section
    raw = config.get(section, "stopwaitsecs", fallback="")
    assert raw, f"{section} must set stopwaitsecs (supervisord default is 10s)"
    value = int(raw)
    assert value >= min_stopwaitsecs, (
      f"{section} stopwaitsecs={value} must be >= {min_stopwaitsecs} "
      "(sync_timedb cooperative drain)"
    )


def test_supervisord_rsync_data_program_uses_wrapper():
  """rsync_data is enabled; supervisord command is the wrapper, not rsync_data.sh."""
  config = configparser.ConfigParser()
  config.read(_supervisord_conf_path())

  assert config.has_section("program:rsync_data")
  command = config.get("program:rsync_data", "command")
  assert "rsync_data_wrapper.sh" in command
  assert command.rstrip().endswith("rsync_data_wrapper.sh")
  assert config.get("program:rsync_data", "user") == "hpcperfstats"
  assert not (_repo_root() / "services-conf" / "supervisord.conf.example").exists()


def test_supervisor_startup_wait_order_is_db_then_redis_then_web():
  repo_root = _repo_root()
  script_path = repo_root / "services-conf" / "supervisor_startup.sh"
  content = script_path.read_text()

  db_marker = "Waiting for postgres..."
  redis_marker = "Waiting for Redis..."
  web_marker = "Waiting for $URL to become available..."

  assert db_marker in content
  assert redis_marker in content
  assert web_marker in content

  assert content.index(db_marker) < content.index(redis_marker)
  assert content.index(redis_marker) < content.index(web_marker)


def test_supervisord_runs_as_hpcperfstats_user():
  """[supervisord] drops to hpcperfstats with user-writable pidfile; no supervisorctl RPC."""
  config = configparser.ConfigParser()
  config.read(_supervisord_conf_path())

  assert config.get("supervisord", "user") == "hpcperfstats"
  pidfile = config.get("supervisord", "pidfile")
  assert pidfile.startswith("/tmp/"), pidfile
  assert "/var/run" not in pidfile

  assert not config.has_section("unix_http_server")
  assert not config.has_section("supervisorctl")
  assert not any(s.startswith("rpcinterface:") for s in config.sections())


def test_supervisord_has_no_root_programs():
  """No program section may set user=root (blocks [supervisord] user= drop)."""
  config = configparser.ConfigParser()
  config.read(_supervisord_conf_path())

  for section in config.sections():
    if not section.startswith("program:"):
      continue
    user = config.get(section, "user", fallback="")
    assert user != "root", section


def test_supervisor_startup_keeps_root_prep_without_exec():
  """Root chown/ssh prep remains; supervisord is launched without exec (PID-1 deferred)."""
  content = (_repo_root() / "services-conf" / "supervisor_startup.sh").read_text()
  assert "chown -R hpcperfstats:hpcperfstats /hpcperfstats/*" in content
  assert "cp /hpcperfstats/.ssh/id*" in content
  assert "/usr/bin/supervisord -c /home/hpcperfstats/services-conf/supervisord.conf" in content
  assert "exec /usr/bin/supervisord" not in content


def test_supervisor_startup_syslog_lines_are_commented_out():
  """Syslog mkdir + render stay as commented re-enable lines, never executed."""
  content = (_repo_root() / "services-conf" / "supervisor_startup.sh").read_text()
  mkdir_hits = [
    line
    for line in content.splitlines()
    if "mkdir -p /var/lib/hpcperfstats-syslog" in line
  ]
  render_hits = [
    line
    for line in content.splitlines()
    if "render_syslog_ng_generated" in line
  ]
  assert mkdir_hits, "expected commented mkdir re-enable line"
  assert render_hits, "expected commented render re-enable line"
  assert all(line.lstrip().startswith("#") for line in mkdir_hits)
  assert all(line.lstrip().startswith("#") for line in render_hits)


def test_rsync_data_wrapper_source_prefers_site_then_example():
  """Wrapper source: prefer rsync_data.sh, else example, else exit 1 (parse only)."""
  text = _rsync_wrapper_path().read_text()
  assert "set -euo pipefail" in text
  assert 'if [ -f "$HERE/rsync_data.sh" ]; then' in text
  assert 'exec /bin/bash "$HERE/rsync_data.sh"' in text
  assert 'if [ -f "$HERE/rsync_data.sh.example" ]; then' in text
  assert 'exec /bin/bash "$HERE/rsync_data.sh.example"' in text
  assert "rsync_data.sh and rsync_data.sh.example missing" in text
  # Site check must appear before example check.
  site_marker = 'if [ -f "$HERE/rsync_data.sh" ]; then'
  example_marker = 'if [ -f "$HERE/rsync_data.sh.example" ]; then'
  assert text.index(site_marker) < text.index(example_marker)


def test_rsync_data_wrapper_executes_site_script_when_present(tmp_path: Path):
  """Behavioral: with stubs only — never run the real 12h payloads."""
  site = tmp_path / "rsync_data.sh"
  example = tmp_path / "rsync_data.sh.example"
  site.write_text("#!/bin/bash\necho SITE\n")
  example.write_text("#!/bin/bash\necho EXAMPLE\n")
  wrapper = tmp_path / "rsync_data_wrapper.sh"
  wrapper.write_text(
    textwrap.dedent(
      """\
      #!/bin/bash
      set -euo pipefail
      HERE="$(cd "$(dirname "$0")" && pwd)"
      if [ -f "$HERE/rsync_data.sh" ]; then
          exec /bin/bash "$HERE/rsync_data.sh"
      fi
      if [ -f "$HERE/rsync_data.sh.example" ]; then
          exec /bin/bash "$HERE/rsync_data.sh.example"
      fi
      echo "rsync_data.sh and rsync_data.sh.example missing" >&2
      exit 1
      """
    )
  )
  wrapper.chmod(0o755)
  proc = subprocess.run(
    ["/bin/bash", str(wrapper)],
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode == 0
  assert proc.stdout.strip() == "SITE"
  assert "EXAMPLE" not in proc.stdout


def test_rsync_data_wrapper_falls_back_to_example(tmp_path: Path):
  example = tmp_path / "rsync_data.sh.example"
  example.write_text("#!/bin/bash\necho EXAMPLE\n")
  wrapper = tmp_path / "rsync_data_wrapper.sh"
  # Copy committed wrapper logic by reading and rewriting HERE-relative paths
  # via running a twin of the committed script in tmp_path.
  committed = _rsync_wrapper_path().read_text()
  wrapper.write_text(committed)
  wrapper.chmod(0o755)
  proc = subprocess.run(
    ["/bin/bash", str(wrapper)],
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode == 0
  assert proc.stdout.strip() == "EXAMPLE"


def test_rsync_data_wrapper_errors_when_both_missing(tmp_path: Path):
  wrapper = tmp_path / "rsync_data_wrapper.sh"
  wrapper.write_text(_rsync_wrapper_path().read_text())
  wrapper.chmod(0o755)
  proc = subprocess.run(
    ["/bin/bash", str(wrapper)],
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode == 1
  assert "missing" in proc.stderr


def _assert_rsync_script_has_top_guard(path: Path) -> None:
  text = path.read_text()
  # Strip shebang and blank/set lines to find first real actions.
  lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
  # After set -x, expect sleep / echo / exit before any rsync.
  assert lines[0] == "set -x"
  assert lines[1] == "sleep 43200"
  assert lines[2] == 'echo "rsync not yet configured"'
  assert lines[3] == "exit"
  first_rsync = text.index("/usr/bin/rsync")
  guard_exit = text.index("\nexit\n")
  assert guard_exit < first_rsync, f"{path.name}: exit guard must precede rsync"
  # No sleep after the first rsync line (in-loop sleep removed).
  after_rsync = text[first_rsync:]
  assert not re.search(r"\nsleep\s+", after_rsync), f"{path.name}: unexpected sleep after rsync"


def test_rsync_data_scripts_have_top_of_script_guard():
  for path in _guarded_rsync_paths():
    assert path.is_file(), path
    _assert_rsync_script_has_top_guard(path)


def test_supervisord_and_rsync_scripts_not_gitignored():
  repo_root = _repo_root()
  for rel in (
    "services-conf/supervisord.conf",
    "services-conf/rsync_data.sh",
    "services-conf/rsync_data_wrapper.sh",
  ):
    proc = subprocess.run(
      ["git", "check-ignore", "-q", rel],
      cwd=repo_root,
      check=False,
    )
    assert proc.returncode != 0, f"{rel} must not be gitignored"
