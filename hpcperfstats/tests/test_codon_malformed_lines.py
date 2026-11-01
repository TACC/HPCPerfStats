"""A malformed raw stats line is skipped by both parsers, never fatal.

One non-numeric token (``6x`` as a counter, a mangled sample-header time, a
bad ``W=`` width) raised an uncaught ``ValueError`` inside the Codon kernel's
``@export`` entry. That aborted the worker process (SIGABRT, parent saw
``codon worker EOF``), every retry died the same way, and after five attempts
the whole raw file was quarantined (stampede3, 2026-11-01). The CPython
parser raised on the same tokens. Both now drop the bad line, or the whole
sample under a bad header, and keep the rest of the file.

Real-pyext cases skip unless ``HPCPERFSTATS_CODON_PYEXT_DIR`` holds a bake of
this tree and the interpreter is free-threaded 3.14t (see
``test_codon_exclude_parity.py``).
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest

SCHEMA = "!cpu user,W=64,U=1 sys,W=64,U=1"

# name -> (lines, parsed host rows, values that must not appear)
CASES: dict[str, tuple[list[str], int, set[float]]] = {
  "bad_value": (
    [
      SCHEMA,
      "1000.0 J1 h",
      "cpu 0 1 2",
      "cpu 1 3 4x",
      "1010.0 J1 h",
      "cpu 0 5 6",
    ],
    4,
    {3.0},
  ),
  "bad_header_drops_its_sample": (
    [
      SCHEMA,
      "1000.0 J1 h",
      "cpu 0 1 2",
      "10x0.0 J1 h",
      "cpu 0 9 9",
      "1020.0 J1 h",
      "cpu 0 5 6",
    ],
    4,
    {9.0},
  ),
  "bad_width_keeps_default": (
    ["!cpu user,W=6x,U=1 sys,W=64,U=1", "1000.0 J1 h", "cpu 0 1 2"],
    2,
    set(),
  ),
  "short_line": ([SCHEMA, "1000.0 J1 h", "cpu 0", "cpu 1 3 4"], 2, set()),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_cpython_parser_skips_malformed_line(name: str) -> None:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    stats_payload_to_records,
  )

  lines, rows, absent = CASES[name]
  parser = IncrementalStatsParser(0)
  with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    parser.feed_lines(lines)
  stats, _procs = parser.finish()
  recs = stats_payload_to_records(stats)
  assert len(recs) == rows
  assert not absent & {float(r["value"]) for r in recs}
  assert {r["wid"] for r in recs} == {64}


# ---- real baked pyext (skipped unless a bake from this tree is mounted) ----

_PYEXT_DIR = os.environ.get("HPCPERFSTATS_CODON_PYEXT_DIR", "").strip()


@pytest.fixture
def pyext() -> Any:
  if not (
    Path(_PYEXT_DIR or "/nonexistent") / "hpcperfstats_codon_parse.so"
  ).is_file():
    pytest.skip("HPCPERFSTATS_CODON_PYEXT_DIR bake required")
  sys.path.insert(0, _PYEXT_DIR)
  try:
    import hpcperfstats_codon_parse as mod
  except ImportError as exc:  # pragma: no cover - env specific
    pytest.skip(f"pyext import failed: {exc}")
  yield mod
  sys.path.remove(_PYEXT_DIR)


@pytest.mark.parametrize("name", sorted(CASES))
def test_codon_line_batch_skips_malformed_line(pyext: Any, name: str) -> None:
  lines, rows, absent = CASES[name]
  result = pyext.parse_stats_lines_codon(lines, 0)
  assert result["parsed_host_rows"] == rows
  assert not absent & set(result["stats"].get("value") or [])


@pytest.mark.parametrize("name", sorted(CASES))
def test_codon_file_session_survives_malformed_line(
  pyext: Any, name: str, tmp_path: Path
) -> None:
  """The file-owned session (production path) returns frames, not abort."""
  lines, rows, _absent = CASES[name]
  path = tmp_path / "stats"
  path.write_text("\n".join(lines) + "\n")
  assert pyext.parse_stats_file_codon_open(str(path), 1 << 20, 0, 0)
  parsed = 0
  while (frame := pyext.parse_stats_file_codon_next(0, 0)) is not None:
    parsed += int(frame["parsed_host_rows"])
  assert parsed == rows
