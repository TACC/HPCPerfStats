import numpy as np

from hpcperfstats.analysis.gen.utils import utils


class _MockHost:
  def __init__(self, stats):
    self.stats = stats


class _MockJob:
  def __init__(self):
    # Two hosts: only host1 has "nvidia_gpu" stats; host2 has none.
    self.schemas = {"nvidia_gpu": ["utilization"]}
    self.hosts = {
        "host1": _MockHost(
            {
                "nvidia_gpu":
                    {
                        "gpu0": np.array([[0.0], [50.0], [100.0]],
                                         dtype=np.float64),
                    }
            }),
        "host2": _MockHost({}),
    }
    # Minimal acct/times required by utils.__init__
    self.acct = {"cores": 1, "nodes": 1}
    self.times = np.array([0.0, 1.0], dtype=np.float64)


def test_get_type_skips_hosts_without_typename():
  """utils.get_type should skip hosts that lack the requested typename instead of raising KeyError."""
  job = _MockJob()
  u = utils(job)

  schema, stats = u.get_type("nvidia_gpu", aggregate=False)

  assert schema is not None
  # Only host1 has nvidia_gpu stats; host2 is skipped.
  assert set(stats.keys()) == {"host1"}
  assert "gpu0" in stats["host1"]
