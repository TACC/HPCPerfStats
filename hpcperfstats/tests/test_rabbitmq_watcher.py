"""Unit tests for pipeline RabbitMQ memory watcher scream."""

from __future__ import annotations

import io
import json
from unittest import mock

import pytest

from hpcperfstats import rabbitmq_watcher as rw


@pytest.mark.parametrize(
    ("mem_gib", "expected"),
    [
        (0.0, None),
        (39.99, None),
        (40.0, 40),
        (40.1, 40),
        (49.9, 40),
        (50.0, 50),
        (59.9, 50),
        (60.0, 60),
        (70.1, 70),
    ],
)
def test_error_threshold_band(mem_gib: float, expected: int | None) -> None:
  assert rw.error_threshold_band(mem_gib) == expected


def test_format_watcher_line_includes_prefix_and_error() -> None:
  below = rw.format_watcher_line(
      mem_used_bytes=9_900_000_000,
      mem_used_gib=9.22,
      connections=2161,
      threshold_gib=None,
  )
  assert below.startswith("[rabbitmq-watcher]")
  assert "ERROR" not in below
  assert "mem_used_gib=9.22" in below
  assert "connections=2161" in below

  above = rw.format_watcher_line(
      mem_used_bytes=45_000_000_000,
      mem_used_gib=42.1,
      connections=100,
      threshold_gib=40,
  )
  assert above.startswith("[rabbitmq-watcher]")
  assert "ERROR" in above
  assert "threshold_gib=40" in above
  assert "mem_used_gib=42.1" in above


def test_fetch_mem_used_parses_nodes_json() -> None:
  payload = [{"name": "rabbit@rabbitmq-prod", "running": True, "mem_used": 123456789}]
  overview = {"object_totals": {"connections": 42}}
  bodies = [
      io.BytesIO(json.dumps(payload).encode()),
      io.BytesIO(json.dumps(overview).encode()),
  ]

  class _Resp:
    def __init__(self, body: io.BytesIO) -> None:
      self._body = body

    def read(self) -> bytes:
      return self._body.read()

    def __enter__(self) -> _Resp:
      return self

    def __exit__(self, *args: object) -> None:
      return None

  with mock.patch(
      "hpcperfstats.rabbitmq_watcher.urllib.request.urlopen",
      side_effect=[_Resp(b) for b in bodies],
  ):
    snap = rw.fetch_node_memory_snapshot(
        base_url="http://rabbitmq:15672",
        user="guest",
        password="guest",
        timeout_s=2.0,
    )
  assert snap.mem_used_bytes == 123456789
  assert snap.connections == 42
  assert snap.node_name == "rabbit@rabbitmq-prod"
