#!/usr/bin/env python3
"""
Source-contract checks for sync-timedb title-orphan fixes.

Attributes:
  ROOT: Absolute path to the HPCPerfStats git checkout.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _populate_entry_ok() -> bool:
  """
  Return whether populate-pool thread entry avoids process-title APIs.

  Returns:
    bool: True when ``_populate_pool_worker_entry`` source omits
    ``apply_ingest_pool_worker_init``, ``apply_pool_worker_process_title``,
    and ``setproctitle``.

  Examples:
    >>> # AST scan of sync_timedb_populate_pool.py in this checkout
    >>> _populate_entry_ok()  # doctest: +SKIP
  """
  path = ROOT / "hpcperfstats/dbload/lib/sync_timedb_populate_pool.py"
  src = path.read_text(encoding="utf-8")
  tree = ast.parse(src)
  for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == (
        "_populate_pool_worker_entry"
    ):
      body = ast.get_source_segment(src, node) or ""
      forbidden = (
          "apply_ingest_pool_worker_init",
          "apply_pool_worker_process_title",
          "setproctitle",
      )
      return all(token not in body for token in forbidden)
  return False


def _hygiene_owner_token_ok() -> bool:
  """
  Return whether lease hygiene passes ``owner_token`` into orphan reconcile.

  Returns:
    bool: True when ``_ingest_runtime_lease_hygiene`` calls
    ``reconcile_this_owner_orphan_leases`` with ``owner_token=`` and
    ``make_lease_owner_token``.

  Examples:
    >>> # Regex slice of sync_timedb_queue_orchestrator.py in this checkout
    >>> _hygiene_owner_token_ok()  # doctest: +SKIP
  """
  path = ROOT / (
      "hpcperfstats/dbload/lib/sync_timedb_queue_orchestrator.py"
  )
  src = path.read_text(encoding="utf-8")
  match = re.search(
      r"def _ingest_runtime_lease_hygiene\([\s\S]*?\n(?=def )",
      src,
  )
  if not match:
    return False
  body = match.group(0)
  return (
      "reconcile_this_owner_orphan_leases" in body
      and "owner_token=" in body
      and "make_lease_owner_token" in body
  )


def main() -> int:
  """
  Exit non-zero when title-orphan source contracts are violated.

  Returns:
    int: ``0`` when both populate-entry and hygiene owner_token checks pass;
    ``1`` otherwise.

  Examples:
    >>> # CLI: python3 scripts/verify_sync_timedb_title_orphan_gates.py
    >>> main()  # doctest: +SKIP
  """
  if not _populate_entry_ok():
    print("FAIL populate_entry_still_calls_process_title", file=sys.stderr)
    return 1
  if not _hygiene_owner_token_ok():
    print("FAIL hygiene_missing_owner_token", file=sys.stderr)
    return 1
  print("title_orphan_gates_ok")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
