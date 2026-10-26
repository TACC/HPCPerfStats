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
    resume_byte: int = 0,
    slot: int = 0,
    exclude: list[str] | None = None,
  ) -> bool:
    from hpcperfstats.dbload.lib.codon_file_bundles import (
      iter_codon_flush_line_bundles,
    )

    try:
      it = iter(
        iter_codon_flush_line_bundles(
          path,
          flush_max_bytes=int(flush_max_bytes),
          resume_byte=int(resume_byte),
        )
      )
    except FileNotFoundError:
      return False
    sessions[int(slot)] = {
      "it": it,
      "carry_raw": {},
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
      lines = next(state["it"])
    except StopIteration:
      return None
    out = parse_stats_lines_codon(
      lines,
      int(slot),
      state["carry_raw"],
      state["carry_arc"],
    )
    state["carry_raw"] = dict(out.get("carry_raw") or {})
    state["carry_arc"] = dict(out.get("carry_arc") or {})
    return out

  mod.parse_stats_file_codon_open = parse_stats_file_codon_open  # type: ignore[attr-defined]
  mod.parse_stats_file_codon_next = parse_stats_file_codon_next  # type: ignore[attr-defined]
