import numpy as np
import pandas as pd

from hpcperfstats.analysis.metrics import metrics


class _FakeJidTable:
  def __init__(self):
    self.jid = 123
    self.host_list = ["host1", "host2"]
    self.schema = {"cpu": ["user", "system"], "mem": ["used"]}

  def get_full_host_data_df(self, columns):
    # Minimal dataset with two hosts, two times, cpu+mem stats
    data = [
        {"host": "host1", "time": "2024-01-01T00:00:00Z", "type": "cpu", "event": "user", "value": 10},
        {"host": "host1", "time": "2024-01-01T00:00:00Z", "type": "cpu", "event": "system", "value": 5},
        {"host": "host2", "time": "2024-01-01T00:05:00Z", "type": "cpu", "event": "user", "value": 20},
        {"host": "host2", "time": "2024-01-01T00:05:00Z", "type": "cpu", "event": "system", "value": 10},
        {"host": "host1", "time": "2024-01-01T00:00:00Z", "type": "mem", "event": "used", "value": 100},
    ]
    df = pd.DataFrame(data)
    # Ensure only requested columns are returned
    return df[columns]


def test_job_for_metrics_builds_time_axis_and_host_stats():
  jt = _FakeJidTable()

  job = metrics._JobForMetrics(jt)

  # Times should be a sorted NumPy array with two unique timestamps.
  assert isinstance(job.times, np.ndarray)
  assert job.times.size == 2
  assert job.hosts.keys() == {"host1", "host2"}
  assert "cpu" in job.schemas
  assert "mem" in job.schemas

  host1_cpu = job.hosts["host1"].stats["cpu"]["agg"]
  # Shape: (n_times, n_events) -> 2 timestamps x 2 events
  assert host1_cpu.shape == (2, 2)

