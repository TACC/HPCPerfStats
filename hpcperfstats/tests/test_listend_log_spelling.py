"""Regression test for the listend consume-start log spelling.

Operators grep pipeline logs for ``Beginning Consume``; a misspelled
literal silently breaks those greps.
"""

from __future__ import annotations

from pathlib import Path

LISTEND_PATH = Path(__file__).resolve().parents[1] / "listend.py"


def test_listend_consume_log_spelled_beginning() -> None:
  """The consume-start log line uses ``Beginning`` and no ``Begining``."""
  source = LISTEND_PATH.read_text(encoding="utf-8")
  assert 'log_print("Beginning Consume from queue: "' in source
  assert "Begining" not in source
