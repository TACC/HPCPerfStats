"""Host-test shim: emulate ``parse_stats_file_codon_open/next`` on fake pyext modules."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any


def attach_parse_stats_file_codon_fake(
  mod: ModuleType,
  parse_stats_lines_codon: Callable[..., dict[str, Any]],
) -> None:
  """
  Bind file-session entrypoints that delegate line batches to ``parse_stats_lines_codon``.

  Production uses the baked Codon kernel (``open``/``readline`` inside ``feed_collapse_capi``).
  Host fakes use ``codon_file_bundles`` only to mirror flush boundaries for tests.
  """
  sessions: dict[int, dict[str, Any]] = {}

  def parse_stats_file_codon_open(
    path: str,
    flush_max_bytes: int,
    start_idx: int = 0,
    slot: int = 0,
    exclude: list[str] | None = None,
    bridge_max: int = -1,
  ) -> bool:
    from hpcperfstats.dbload.lib.codon_file_bundles import (
      iter_codon_flush_line_bundles,
    )

    try:
      it = iter(
        iter_codon_flush_line_bundles(
          path,
          flush_max_bytes=int(flush_max_bytes),
          start_idx=int(start_idx),
        )
      )
    except FileNotFoundError:
      return False
    # Open clears the slot carry, then seeds the sentinel bridge limit.
    carry_raw: dict[str, list[float]] = {}
    if bridge_max >= 0:
      carry_raw["\0cfg\0bridge_max"] = [float(bridge_max)]
    sessions[int(slot)] = {
      "it": it,
      "carry_raw": carry_raw,
      "carry_arc": {},
      "exclude": list(exclude) if exclude else [],
    }
    return True

  def parse_stats_file_codon_next(
    slot: int = 0,
    emit_insert: int = 0,
  ) -> dict[str, Any] | None:
    del emit_insert
    state = sessions.get(int(slot))
    if state is None:
      return None
    try:
      lines, rel_start = next(state["it"])
    except StopIteration:
      return None
    # Fakes predating the resume gate take no ``start_idx``; pass it only
    # when the bundle has a resume prefix.
    gate = {"start_idx": rel_start} if rel_start else {}
    out = parse_stats_lines_codon(
      lines,
      int(slot),
      state["carry_raw"],
      state["carry_arc"],
      **gate,
    )
    state["carry_raw"] = dict(out.get("carry_raw") or {})
    state["carry_arc"] = dict(out.get("carry_arc") or {})
    return out

  mod.parse_stats_file_codon_open = parse_stats_file_codon_open  # type: ignore[attr-defined]
  mod.parse_stats_file_codon_next = parse_stats_file_codon_next  # type: ignore[attr-defined]
