#!/usr/bin/env python3
"""
Test runner for hpcperfstats. Runs pytest over the package.

Use --no-django for host-safe tests without ``django_db`` markers; marked
Django tests need PostgreSQL and HPCPERFSTATS_INI.

Attributes:
  _root: Attribute.
"""
from __future__ import annotations

from typing import Any

import os
import sys

# Run from directory containing pyproject.toml.
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_root)
if _root not in sys.path:
  sys.path.insert(0, _root)


def main() -> Any:
  """
  Run pytest, optionally excluding tests marked for Django database access.
  
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
    host_args = [
        "--ignore=hpcperfstats/site/lib/machine/tests",
        "-m",
        "not django_db",
        "-q",
    ]
    args = host_args + (args or ["hpcperfstats"])
  else:
    args = args or ["-v", "hpcperfstats"]
  return pytest.main(args)


if __name__ == "__main__":
  sys.exit(main())
