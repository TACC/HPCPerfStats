"""Importable fake Codon pyext for the real ``codon_process_worker`` subprocess.

``parse_stats_file_codon_next`` returns one COPY-bytes frame per input line:
``host_copy = "<pid>|<exclude>|<emit_insert>|<line>"`` so tests can assert what
crossed the process boundary. An empty file yields no frames.
"""

from __future__ import annotations

import os
from typing import Any

_sessions: dict[int, dict[str, Any]] = {}


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
  return {
    "stats": {"host_copy": body, "proc_copy": b""},
    "procs": {},
    "marks": [],
    "finished": True,
    "copy_unique": True,
  }
