"""Tests for compose CPU partition helper."""

import pytest

from hpcperfstats.dbload.lib.compose_cpu_layout import partition_responsive_cpusets


def _cpus_in_cpuset(s: str) -> set:
  out = set()
  for part in s.split(","):
    part = part.strip()
    if "-" in part:
      lo, hi = part.split("-", 1)
      out.update(range(int(lo), int(hi) + 1))
    else:
      out.add(int(part))
  return out


def _covered_cpus(parts: dict) -> set:
  u = set()
  for key in ("db", "web", "redis", "rabbitmq", "pipeline"):
    u |= _cpus_in_cpuset(parts[key])
  return u


@pytest.mark.parametrize("n", [8, 16, 32, 40, 64])
def test_partition_covers_all_cpus(n):
  p = partition_responsive_cpusets(n)
  assert p["proxy"] == p["web"]
  assert _covered_cpus(p) == set(range(n))


def test_partition_pipeline_nonempty_for_typical_hosts():
  for n in range(4, 128):
    p = partition_responsive_cpusets(n)
    assert _cpus_in_cpuset(p["pipeline"])


def test_partition_small_hosts_shared_pool():
  p = partition_responsive_cpusets(2)
  assert p["db"] == p["pipeline"] == "0-1"


def test_value_error_on_zero():
  with pytest.raises(ValueError):
    partition_responsive_cpusets(0)
