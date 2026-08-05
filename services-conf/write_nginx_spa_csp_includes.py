#!/usr/bin/env python3
"""
CLI: write private SPA CSP nginx includes from on-volume HTML.

Logic lives in ``spa_csp_meta`` (proxy image sibling module, or
``hpcperfstats.site.lib.spa_csp_meta`` on the web image).
"""

from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path


def _load_spa_csp_meta() -> types.ModuleType:
  """
  Import CSP helpers from the proxy sibling module or the Django package.

  Returns:
    types.ModuleType: Loaded ``spa_csp_meta`` module.

  Raises:
    ImportError: When neither import path is available.

  Examples:
    >>> mod = _load_spa_csp_meta()  # doctest: +SKIP
  """
  try:
    import spa_csp_meta as mod  # type: ignore[import-not-found]

    return mod
  except ImportError:
    from hpcperfstats.site.lib import spa_csp_meta as mod

    return mod


def main(argv: list[str] | None = None) -> int:
  """
  Regenerate private ``/etc/nginx`` SPA CSP includes from volume HTML.

  Args:
    argv (list[str] | None): Optional argument vector (defaults to ``sys.argv[1:]``).

  Returns:
    int: Process exit code (0 on success).

  Examples:
    >>> 0
    0
  """
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--frontend-root",
      type=Path,
      default=Path("/srv/static/frontend"),
      help="SPA static export root (read-only HTML source on proxy)",
  )
  parser.add_argument(
      "--out-dir",
      type=Path,
      default=Path("/etc/nginx"),
      help="Private directory for nginx-csp-*.inc (never under /srv/static)",
  )
  args = parser.parse_args(argv)
  mod = _load_spa_csp_meta()
  try:
    machine_out, pub_out = mod.write_spa_csp_includes(args.frontend_root, args.out_dir)
  except FileNotFoundError as exc:
    print(f"write_nginx_spa_csp_includes: {exc}", file=sys.stderr)
    return 1
  print(f"write_nginx_spa_csp_includes: wrote {machine_out} and {pub_out}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
