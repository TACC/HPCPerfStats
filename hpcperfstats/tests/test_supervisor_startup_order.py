from __future__ import annotations

from pathlib import Path


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

