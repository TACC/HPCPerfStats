"""Contract tests for baked pipeline 3.14t vs GIL web interpreters."""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _supervisord_text() -> str:
  return (_repo_root() / "services-conf" / "supervisord.conf").read_text()


def _program_command(text: str, program: str) -> str:
  match = re.search(
      rf"^\[program:{re.escape(program)}\]\n(?:.*\n)*?^command=(.+)$",
      text,
      flags=re.MULTILINE,
  )
  assert match, f"program {program} not found"
  return match.group(1)


def test_pipeline_programs_use_opt_python314t():
  """listend / sync_timedb / update_metrics must bake /opt/python3.14t."""
  text = _supervisord_text()
  ft = "/opt/python3.14t/bin/python"
  for program in (
      "hpcperfstats-rabbitmq-listener",
      "sync_timedb",
      "update_metrics",
  ):
    command = _program_command(text, program)
    assert ft in command, (program, command)
    assert "/usr/local/bin/python3" not in command, (program, command)


def test_supervisord_has_no_syslog_programs():
  """syslog-ng and seal_syslog_daily are not supervisord programs."""
  text = _supervisord_text()
  assert "[program:syslog-ng]" not in text
  assert "[program:seal_syslog_daily]" not in text
  assert "syslog-ng" not in text
  assert "seal_syslog_daily" not in text


def test_supervisord_has_no_pipeline_interpreter_resolver():
  """No INI/env ABI switch — image bake only."""
  text = _supervisord_text()
  assert "pipeline_interpreter" not in text
  assert "listend_interpreter" not in text
  assert "sync_timedb_interpreter" not in text
  assert "update_metrics_interpreter" not in text
