"""Importable fake Codon pyext for the real ``codon_process_worker`` subprocess.

``parse_stats_file_codon_next`` returns one COPY-bytes frame per input line:
``host_copy = "<pid>|<exclude>|<emit_insert>|<line>"`` so tests can assert what
crossed the process boundary. An empty file yields no frames. Each frame
reports ``parsed_host_rows=2``; a ``parsed=N`` line yields an empty host COPY
frame that still parsed ``N`` host rows (all collapsed away), and an ``hp``
line adds ``proc_copy`` so coalescing splits it.
"""

from __future__ import annotations

import os
from typing import Any

_sessions: dict[int, dict[str, Any]] = {}


def feed_and_collapse_fingerprint(lines, slot=0):
  return (0, 0, 0.0, 0.0)


def parse_stats_lines_codon(
  lines, slot=0, carry_raw=None, carry_arc=None, exclude=None
):
  return {"stats": {}, "procs": {}}


def parse_stats_file_codon_open(path, flush, _resume=0, slot=0, exclude=None):
  with open(path, encoding="utf-8") as fh:
    lines = fh.read().splitlines()
  _sessions[slot] = {"lines": lines, "i": 0, "exclude": ",".join(exclude or [])}
  return True


def parse_stats_file_codon_next(slot=0, emit_insert=0):
  sess = _sessions[slot]
  if sess["i"] >= len(sess["lines"]):
    return None
  line = sess["lines"][sess["i"]]
  sess["i"] += 1
  body = f"{os.getpid()}|{sess['exclude']}|{emit_insert}|{line}".encode()
  if line == "big":
    body += b"x" * 300_000
  parsed = 2
  if line.startswith("parsed="):
    body, parsed = b"", int(line.split("=", 1)[1])
  return {
    "stats": {"host_copy": body, "proc_copy": b"p\n" if line == "hp" else b""},
    "parsed_host_rows": parsed,
    "procs": {},
    "marks": [],
    "finished": True,
    "copy_unique": True,
  }
