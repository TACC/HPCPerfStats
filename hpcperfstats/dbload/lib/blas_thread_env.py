"""
Cap BLAS/OpenMP threads before numpy/pandas import (spawn pool workers).

Attributes:
  BLAS_THREAD_ENV_KEYS: Attribute.
"""
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
  """
  Set thread caps with ``setdefault`` so operator env overrides remain valid.
  
  Args:
    default (str): String for default.
  
  Returns:
    None
  
  Examples:
    >>> configure_blas_thread_env("x")  # doctest: +SKIP
  """
  value = str(default)
  for key in BLAS_THREAD_ENV_KEYS:
    os.environ.setdefault(key, value)
