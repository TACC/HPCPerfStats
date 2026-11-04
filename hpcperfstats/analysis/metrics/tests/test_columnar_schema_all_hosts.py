"""The columnar job schema covers every type fetched from any host.

Regression for job 79282 (hpcperfstats04, 2026-11-03): ``jid_table`` probes
one host (``host_list[0]``) for the schema. That host had no GPUs, so the
4-GPU node's ``nvidia_gpu`` rows were fetched but never reached the
schema-gated metrics.
"""

from __future__ import annotations

from hpcperfstats.analysis.metrics.lib.columnar.job_table import (
  ColumnarJobTable,
)
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame


def test_columnar_schema_includes_every_fetched_type() -> None:
  """A GPU host that is not ``host_list[0]`` still adds ``nvidia_gpu``."""
  frame = JobFrame.from_records(
    "79282",
    [
      (1_000_000, "cpu-host", "host_cpu", "user", None, 1, 5.0, 1.0, 1.0),
      (1_000_000, "gpu-host", "nvidia_gpu", "gpu_util", "0", 1, 90.0, 0, 0),
    ],
  )
  table = ColumnarJobTable(
    "79282",
    frame,
    host_list=["cpu-host", "gpu-host"],
    acct_host_list=["cpu-host", "gpu-host"],
    schema={"host_cpu": ["user", "system"]},
    start_time=None,
    end_time=None,
  )
  # jid_table's own event order is kept; fetched types are appended.
  assert table.schema == {
    "host_cpu": ["user", "system"],
    "nvidia_gpu": ["gpu_util"],
  }
