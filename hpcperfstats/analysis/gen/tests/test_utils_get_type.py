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


def test_amd64_pmc_sets_pmc_and_freq_for_get_type():
  """AMD jobs expose amd64_pmc as logical 'pmc' like Intel typenames."""
  job = _MockJob()
  job.schemas = {"amd64_pmc": ["FLOPS", "APERF"]}
  job.hosts = {
      "h1": _MockHost({
          "amd64_pmc": {
              "0": np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float64),
          }
      }),
  }
  u = utils(job)
  assert u.pmc == "amd64_pmc"
  assert u.freq == 2.7
  schema, stats = u.get_type("pmc", aggregate=True)
  assert schema is not None
  assert "h1" in stats


def test_cpu_counter_metrics_sets_pmc_for_get_type():
  """LIKWID cpu_counter_metrics is treated as the logical PMC typename for plots."""
  job = _MockJob()
  job.schemas = {"cpu_counter_metrics": ["INST_RETIRED", "APERF"]}
  job.hosts = {
      "h1": _MockHost({
          "cpu_counter_metrics": {
              "0": np.array([[0.0, 0.0], [1.0, 2.0]], dtype=np.float64),
          }
      }),
  }
  u = utils(job)
  assert u.pmc == "cpu_counter_metrics"
  assert u.freq == 2.7
