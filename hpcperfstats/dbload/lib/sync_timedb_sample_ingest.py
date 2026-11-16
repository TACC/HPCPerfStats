"""
Shared monitor sample text → post-delta host/proc dataframes for write stage.

Used by listend live parse threads and keeps the same whole-sample skip
contract as the former listend ``_process_sample_to_orm`` path. Frames are
the payload shape expected by ``IngestWritePayload`` /
``_write_stats_payload_to_db``.

Attributes:
  sample_measurement_types: Typed measurement names after a digit header.
  schema_covers_measurement_types: Schema completeness for non-proc types.
  ingest_monitor_sample_text: Parse one sample to flush-ready frames.
"""

from __future__ import annotations

from typing import Any


def sample_measurement_types(message: str) -> list[str]:
  """
  Collect alpha-leading typed measurement names after a digit timestamp header.

  Args:
    message (str): Raw monitor sample text (one or more lines).

  Returns:
    list[str]: Measurement type tokens in encounter order.

  Examples:
    >>> sample_measurement_types("1710000001.0 1 host\\ncpuuser - 1\\n")
    ['cpuuser']
  """
  types: list[str] = []
  saw_ts = False
  for line in message.splitlines():
    s = line.lstrip()
    if not s:
      continue
    if s[0].isdigit():
      saw_ts = True
      continue
    if saw_ts and s[0].isalpha():
      types.append(s.split(maxsplit=1)[0])
  return types


def schema_covers_measurement_types(schema: dict, types: list[str]) -> bool:
  """
  Return True when ``schema`` covers every non-proc measurement in ``types``.

  Args:
    schema (dict): Host measurement schema map (type → event list).
    types (list[str]): Measurement types from :func:`sample_measurement_types`.

  Returns:
    bool: False when any non-proc/host_proc type is missing from ``schema``.

  Examples:
    >>> schema_covers_measurement_types({"cpu": ["user"]}, ["cpu"])
    True
    >>> schema_covers_measurement_types({}, ["cpu"])
    False
  """
  for typ in types:
    if typ in ("proc", "host_proc"):
      continue
    if typ not in schema:
      return False
  return True


def ingest_monitor_sample_text(
  message: str,
  *,
  schema: dict,
  schema_fast: dict,
  carry: Any,
) -> tuple[Any, Any]:
  """
  Parse one complete monitor sample into post-delta host/proc dataframes.

  Fail-closed: missing schema coverage, parse failure, or incomplete host
  payload yields empty frames (no partial host insert). Mutates ``carry``
  when host stats are finalized.

  Args:
    message (str): Complete UTF-8 monitor sample text.
    schema (dict): Injected ``!`` schema for this host.
    schema_fast (dict): Fast-schema sibling map for ``@fast`` keys.
    carry (Any): ``DeltaCarryState`` (or None) owned by the caller; when
      None and host rows exist, a new ``DeltaCarryState`` is created locally
      and discarded (caller should pass a durable carry for multi-sample
      hosts).

  Returns:
    tuple[Any, Any]: ``(stats_df, proc_df)`` suitable for
    ``IngestWritePayload``; either may be empty.

  Examples:
    >>> import pandas as pd
    >>> from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    ...   DeltaCarryState,
    ... )
    >>> stats, proc = ingest_monitor_sample_text(
    ...   "1710000001.0 1 h\\n",
    ...   schema={},
    ...   schema_fast={},
    ...   carry=DeltaCarryState(),
    ... )
    >>> isinstance(stats, pd.DataFrame) and isinstance(proc, pd.DataFrame)
    True
  """
  import pandas as pd

  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    DeltaCarryState,
    IncrementalStatsParser,
    build_stats_dataframes,
    compute_deltas_and_arc_chunk,
    stats_payload_row_count,
  )

  empty = pd.DataFrame(), pd.DataFrame()
  types = sample_measurement_types(message)
  if not types:
    return empty
  if not schema_covers_measurement_types(schema, types):
    return empty

  from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import (
    codon_ingest_parse_enabled,
    try_codon_parse_collapse,
  )

  local_carry = carry if carry is not None else DeltaCarryState()
  # Codon finishes delta/collapse/arc (and persists marks) when enabled at
  # boot; the cached schema travels as ``!`` lines ahead of the sample.
  codon = codon_ingest_parse_enabled()
  try:
    if codon:
      bang = [f"!{typ} {' '.join(tokens)}" for typ, tokens in schema.items()]
      stats_cols, proc_list = try_codon_parse_collapse(
        bang + message.splitlines(), carry=local_carry
      )
    else:
      parser = IncrementalStatsParser(0)
      parser.schema = dict(schema)
      parser.schema_fast = dict(schema_fast)
      parser.compile_injected_schema()
      parser.feed_lines(message.splitlines())
      from hpcperfstats.dbload.lib.monitor_mark_side_ingest import (
        flush_parser_side_marks,
      )

      flush_parser_side_marks(parser)
      stats_cols = parser.take_stats_columns()
      proc_list = parser.proc_stats
  except Exception:
    return empty

  proc_n = (
    len(next(iter(proc_list.values()), ()))
    if isinstance(proc_list, dict)
    else len(proc_list or ())
  )
  non_proc = [t for t in types if t not in ("proc", "host_proc")]
  if non_proc and stats_payload_row_count(stats_cols) == 0:
    return empty
  if stats_payload_row_count(stats_cols) == 0 and not proc_n:
    return empty

  stats_df, proc_df = build_stats_dataframes(stats_cols, proc_list)
  del stats_cols, proc_list
  if not stats_df.empty and not codon:
    stats_df = compute_deltas_and_arc_chunk(stats_df, carry=local_carry)
  return stats_df, proc_df
