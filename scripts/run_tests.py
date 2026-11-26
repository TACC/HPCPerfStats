#!/usr/bin/env python3
"""
Test runner for hpcperfstats. Runs pytest over the package.

Use --no-django to deselect the ``compose_db`` tests that need the default
PostgreSQL database (marked by ``hpcperfstats/conftest.py``); everything
else, including ``django_db(databases=[])`` mock tests, still runs.

Attributes:
  _root: Attribute.
"""

from __future__ import annotations

import os
import sys
from typing import Any

# Run from directory containing pyproject.toml.
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_root)
if _root not in sys.path:
  sys.path.insert(0, _root)


def main() -> Any:
  """
  Run pytest, optionally deselecting tests that need the default database.

  Returns:
    Any: Open return polymorphism from ``main``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).

  Examples:
    >>> main()  # doctest: +SKIP
  """
  import pytest

  args = list(sys.argv[1:])
  if "--no-django" in args:
    args.remove("--no-django")
    args = ["-m", "not compose_db", "-q"] + (args or ["hpcperfstats"])
  else:
    args = args or ["-v", "hpcperfstats"]
  return pytest.main(args)


if __name__ == "__main__":
  sys.exit(main())
