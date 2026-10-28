"""Compose contract: checkpoint / WAL / bgwriter GUCs on db (PG15) and db_pg18."""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SHARED_GUC_MARKERS = (
  "checkpoint_timeout=30min",
  "checkpoint_completion_target=0.9",
  "min_wal_size=4GB",
  "max_wal_size=64GB",
  "wal_buffers=128MB",
  "bgwriter_lru_maxpages=800",
  "bgwriter_lru_multiplier=5",
  "maintenance_io_concurrency=64",
)


def _service_block(service: str) -> str:
  content = (_REPO_ROOT / "docker-compose.defaults.yaml").read_text()
  match = re.search(rf"(?ms)^  {service}:\n(.*?)(?=^  [a-z].*:|\Z)", content)
  assert match, f"{service} service not found"
  return match.group(0)


def test_docker_compose_db_pg15_checkpoint_io_gucs() -> None:
  """Hub PG15 uses wal_compression=lz4 (image has lz4); shares checkpoint tuning with pg18."""
  block = _service_block("db")
  assert "timescale/timescaledb:2.28.3-pg15" in block
  for marker in _SHARED_GUC_MARKERS:
    assert marker in block, marker
  assert "wal_compression=lz4" in block
  assert re.search(r"wal_compression=on\b", block) is None
  assert "io_method=io_uring" not in block
  # 64GB buffer pool on the 377 GiB ingest host (db has no mem_limit); the
  # profile showed ~4-5% of Postgres CPU in OS page-cache read/copy at 12GB.
  assert "shared_buffers=64GB" in block


def test_docker_compose_db_pg18_checkpoint_io_gucs() -> None:
  """Homemade PG18 uses wal_compression=lz4 plus shared checkpoint tuning."""
  block = _service_block("db_pg18")
  assert "image: hpcperfstats-db" in block
  for marker in _SHARED_GUC_MARKERS:
    assert marker in block, marker
  assert "wal_compression=lz4" in block
  assert re.search(r"wal_compression=on\b", block) is None
  assert "io_method=io_uring" in block
