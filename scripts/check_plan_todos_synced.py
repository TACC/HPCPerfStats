#!/usr/bin/env python3
"""Fail when a live plan YAML frontmatter still has pending todos."""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
  """
  Exit 0 when the given plan has no ``status: pending`` todos.

  Args:
    argv (list[str] | None): CLI args; defaults to ``sys.argv[1:]``.

  Returns:
    int: ``0`` when synced, ``1`` when pending todos remain.

  Examples:
    >>> Path("x").suffix
    '.md'
  """
  args = list(sys.argv[1:] if argv is None else argv)
  if not args or args[0] in ("-h", "--help"):
    print("usage: check_plan_todos_synced.py <plan.md>")
    return 2
  path = Path(args[0])
  text = path.read_text(encoding="utf-8")
  if not text.startswith("---"):
    print("no YAML frontmatter: %s" % path)
    return 1
  rest = text[3:]
  end = rest.find("\n---")
  if end < 0:
    print("unclosed YAML frontmatter: %s" % path)
    return 1
  front = rest[:end]
  pending = [
      line.strip()
      for line in front.splitlines()
      if line.strip() == "status: pending"
  ]
  if pending:
    print("pending todos remain: %d" % len(pending))
    return 1
  print("PLAN_TODOS_SYNCED_OK")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
