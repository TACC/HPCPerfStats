"""Drift guard: the Redis -> Valkey cutover left no project-owned redis names.

The compose cache is Valkey and the Python clients are valkey-py +
django-valkey, so no tracked file may name redis except: guards that forbid
retired Redis pieces, the one-shot INI rename migration, the admin-stats
regression fixture, and dated incident records that describe what ran then.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

_REDIS_RE = re.compile(
  r"\bredis\b|redis_|_redis|rediswait|REDIS|Redis\b|redis-|redis\.|/redis"
)
# Words that merely contain "redis" (redispatch, rediscover, redistribute).
_FALSE_POSITIVE_RE = re.compile(r"(?i)redis(?:patch|cover|tribut)\w*")

# Whole files allowed to name redis, each with the reason it must.
_ALLOWED_PATHS = {
  # Guards that forbid retired Redis modules / Lua / keys from returning.
  "hpcperfstats/tests/test_sync_timedb_architecture_contract.py",
  "hpcperfstats/tests/test_sync_timedb_job_queue.py",
  "hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py",
  "hpcperfstats/tests/test_invalidate_archive_members_cli.py",
  "hpcperfstats/tests/test_sync_timedb_archive_members_store_required.py",
  # This guard itself.
  "hpcperfstats/tests/test_valkey_rename_drift.py",
  # One-shot site INI migration (redis_location -> valkey_location) + tests.
  "scripts/rewrite_hpcperfstats_ini.py",
  "hpcperfstats/tests/test_rewrite_hpcperfstats_ini.py",
  # Admin cache-stats fixture proves Valkey's compat redis_version is ignored.
  "hpcperfstats/site/lib/machine/tests/test_api_helpers.py",
  # Live test asserts the compat field is not surfaced.
  "hpcperfstats/site/lib/machine/tests/test_valkey_cache_live.py",
  # Operator cutover instructions and dated incident / audit records.
  "docs/upgrade.md",
  "docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md",
  "docs/artifacts/update_metrics_stall_audit_2026-05-12.md",
  "docs/SECURITY_AUDIT.md",
  "docs/SECURITY_REMEDIATION_BACKLOG.md",
  "hpcperfstats/cursor-rules/operator-command-lessons-learned.mdc",
  "hpcperfstats/cursor-rules/compose-operator-terminal-commands.mdc",
  "hpcperfstats/cursor-rules/python-image-interpreter-contract.mdc",
}
# Separate distributions / trees with their own history.
_EXCLUDED_PREFIXES = ("monitor/",)


def _tracked_text_files() -> list[str]:
  out = subprocess.run(
    ["git", "ls-files", "-z"],
    cwd=_REPO,
    check=True,
    capture_output=True,
  ).stdout.decode()
  return [
    p for p in out.split("\0") if p and not p.startswith(_EXCLUDED_PREFIXES)
  ]


def _redis_hits(text: str) -> list[str]:
  return _REDIS_RE.findall(_FALSE_POSITIVE_RE.sub("", text))


def test_redis_pattern_positive_and_negative_controls():
  """The scanner must flag real names and ignore look-alike words."""
  assert _redis_hits("redis_location = x")
  assert _redis_hits("import redis")
  assert _redis_hits("the Redis server")
  assert _redis_hits("/run/redis/redis.sock")
  assert not _redis_hits("redispatch REDISCOVER redistribute valkey")


def test_no_project_redis_names():
  failures = []
  for rel in _tracked_text_files():
    if rel in _ALLOWED_PATHS:
      continue
    path = _REPO / rel
    try:
      text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError, IsADirectoryError, FileNotFoundError:
      continue
    for lineno, line in enumerate(text.splitlines(), 1):
      if _redis_hits(line):
        failures.append(f"{rel}:{lineno}: {line.strip()[:120]}")
  assert not failures, "redis names left after the Valkey cutover:\n" + (
    "\n".join(failures[:80])
  )


def test_allowlisted_paths_still_exist():
  """A stale allowlist entry would hide a future rename."""
  missing = [p for p in sorted(_ALLOWED_PATHS) if not (_REPO / p).is_file()]
  assert not missing, missing


@pytest.mark.parametrize(
  "pattern", [r"^\s*import redis\b", r"^\s*from redis\b"]
)
def test_no_python_imports_redis_py(pattern):
  rx = re.compile(pattern, re.M)
  offenders = [
    rel
    for rel in _tracked_text_files()
    if rel.endswith(".py")
    and rel != "hpcperfstats/tests/test_valkey_rename_drift.py"
    and rx.search((_REPO / rel).read_text(encoding="utf-8", errors="replace"))
  ]
  assert not offenders, offenders


def test_pyproject_depends_on_valkey_clients_not_redis_py():
  text = (_REPO / "pyproject.toml").read_text()
  assert '"django-valkey==' in text
  assert '"valkey==' in text
  assert not re.search(r'^\s*"redis[=<>~!\[]', text, re.M)
