from __future__ import annotations

import configparser
from pathlib import Path


def _supervisord_example_path() -> Path:
  return Path(__file__).resolve().parents[2] / "services-conf" / "supervisord.conf.example"


def test_supervisord_hpcperfstats_programs_set_home_and_user_environment():
  """Supervisord setuid does not update HOME; bash -lc must not inherit /root."""
  config = configparser.ConfigParser()
  config.read(_supervisord_example_path())

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


def test_supervisor_startup_wait_order_is_db_then_redis_then_web():
  repo_root = Path(__file__).resolve().parents[2]
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

