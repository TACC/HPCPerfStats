#!/usr/bin/env python3
"""Test runner for hpcperfstats. Runs pytest over the package. Use --no-django for unit tests only; Django tests need PostgreSQL and HPCPERFSTATS_INI.

AI generated.
"""
import os
import sys

# Run from directory containing pyproject.toml
_root = os.path.dirname(os.path.abspath(__file__))
os.chdir(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)


def main():
    """Run pytest with optional --no-django; return exit code from pytest.main.

    AI generated.
    """
    import pytest
    args = list(sys.argv[1:])
    if "--no-django" in args:
        args.remove("--no-django")
        args.extend(["--ignore=hpcperfstats/site/machine/tests", "-v"])
    else:
        args = args or ["-v", "hpcperfstats"]
    return pytest.main(args)


if __name__ == "__main__":
    sys.exit(main())
