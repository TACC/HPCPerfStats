#!/usr/bin/env python3
"""Unlazy gate helpers for S4 dead-code / dual-mode cleanup."""
from __future__ import annotations

import pathlib
import sys


def g1() -> None:
  from hpcperfstats.dbload import sync_timedb as st

  for mode in ("backlog", "current"):
    try:
      st.parse_sync_timedb_argv(["sync_timedb.py", mode])
    except SystemExit as exc:
      text = str(exc).lower()
      if "retired" not in text and mode not in text:
        raise SystemExit("unexpected SystemExit for %s: %s" % (mode, exc))
    else:
      raise SystemExit("%s accepted" % mode)
  src = pathlib.Path("hpcperfstats/dbload/sync_timedb.py").read_text(
      encoding="utf-8",
  )
  if "startdate == 'backlog'" in src or 'startdate == "backlog"' in src:
    raise SystemExit("backlog branch still present")
  if "elif startdate == 'current':" in src:
    raise SystemExit("current branch still present")
  print("G1 CLI dual-mode retired")


def main(argv: list[str]) -> int:
  if argv == ["g1"]:
    g1()
    return 0
  raise SystemExit("usage: unlazy_s4_deadcode_gates.py g1")


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
