"""Tests for BLAS/OpenMP thread caps before numpy under spawn."""
import os

import hpcperfstats.dbload.lib.blas_thread_env as blas_env


def test_configure_blas_thread_env_idempotent(monkeypatch):
  for key in blas_env.BLAS_THREAD_ENV_KEYS:
    monkeypatch.delenv(key, raising=False)
  blas_env.configure_blas_thread_env()
  assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
  assert os.environ["OMP_NUM_THREADS"] == "1"
  monkeypatch.setenv("OPENBLAS_NUM_THREADS", "7")
  blas_env.configure_blas_thread_env()
  assert os.environ["OPENBLAS_NUM_THREADS"] == "7"


def test_sync_timedb_import_sets_blas_before_numpy(monkeypatch):
  for key in blas_env.BLAS_THREAD_ENV_KEYS:
    monkeypatch.delenv(key, raising=False)
  import importlib

  import hpcperfstats.dbload.sync_timedb as sync_timedb_mod

  importlib.reload(sync_timedb_mod)
  assert os.environ.get("OPENBLAS_NUM_THREADS") == "1"
  assert os.environ.get("OMP_NUM_THREADS") == "1"
