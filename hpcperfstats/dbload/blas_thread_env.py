"""Cap BLAS/OpenMP threads before numpy/pandas import (spawn pool workers)."""
from __future__ import annotations

import os

BLAS_THREAD_ENV_KEYS = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def configure_blas_thread_env(*, default: str = "1") -> None:
  """Set thread caps with ``setdefault`` so operator env overrides remain valid."""
  value = str(default)
  for key in BLAS_THREAD_ENV_KEYS:
    os.environ.setdefault(key, value)


def apply_archive_metadata_pool_worker_init(script_name, pool_kind):
  """Picklable metadata-pool initializer: BLAS cap + process title."""
  configure_blas_thread_env()
  from hpcperfstats.process_title import apply_pool_worker_process_title

  apply_pool_worker_process_title(script_name, pool_kind)
