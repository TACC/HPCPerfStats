"""Codon stdio worker must skip interpreter finalization after stdin EOF.

hpcperfstats04 2026-10-27: after the parent died, 40 workers had read EOF,
left ``main()`` and then spun at 100% CPU in ``sched_yield`` during shutdown
(Codon/Boehm runtime threads), never exiting.
"""

from __future__ import annotations

import io
import sys

from hpcperfstats.dbload.lib import codon_process_worker as cpw


def test_run_worker_hard_exits_after_eof(monkeypatch):
  exits = []
  monkeypatch.setattr(cpw.os, "_exit", lambda code: exits.append(code))
  monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
  cpw.run_worker()
  assert exits == [0]


def test_run_worker_flushes_stdio_before_exit(monkeypatch):
  order = []

  class _Out(io.StringIO):
    def flush(self):
      order.append("flush")

  monkeypatch.setattr(sys, "stdout", _Out())
  monkeypatch.setattr(cpw.os, "_exit", lambda code: order.append("exit"))
  monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
  cpw.run_worker()
  assert order[-1] == "exit"
  assert "flush" in order
