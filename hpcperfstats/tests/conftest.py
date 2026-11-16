"""Fixtures shared by ``hpcperfstats/tests``."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def pyext() -> Iterator[Any]:
  """Baked Codon parse pyext from ``HPCPERFSTATS_CODON_PYEXT_DIR``.

  Loads it the way the bridge does: Codon's ``libomp`` is deep-bound first,
  or, with NumPy/MKL already imported, the interpreter hangs at exit after
  the tests pass. Skips when no bake is mounted.
  """
  pyext_dir = os.environ.get("HPCPERFSTATS_CODON_PYEXT_DIR", "").strip()
  if not (
    pyext_dir and (Path(pyext_dir) / "hpcperfstats_codon_parse.so").is_file()
  ):
    pytest.skip("HPCPERFSTATS_CODON_PYEXT_DIR bake required")
  from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import (
    _deepbind_codon_libomp,
  )

  _deepbind_codon_libomp(pyext_dir)
  sys.path.insert(0, pyext_dir)
  try:
    import hpcperfstats_codon_parse as mod
  except ImportError as exc:  # pragma: no cover - env specific
    pytest.skip(f"pyext import failed: {exc}")
  yield mod
  sys.path.remove(pyext_dir)
