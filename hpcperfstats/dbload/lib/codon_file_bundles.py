"""Host-test flush windows only (mirrors kernel batch boundaries).

Production divert-on file parse uses ``parse_stats_file_codon_open/next`` in the
baked Codon kernel (`feed_collapse_capi.py`). This module is for host fakes in
``codon_file_session_fake`` — not imported by ``codon_process_worker`` on the hot path.
"""

from __future__ import annotations

from collections.abc import Iterator


def _decode_stats_line(raw: bytes) -> str:
  """
  Decode one stats-file line to text.

  Args:
    raw (bytes): Line bytes, or text that is already decoded.

  Returns:
    str: ASCII text; invalid bytes are replaced.

  Examples:
    >>> _decode_stats_line(b"cpu 0")
    'cpu 0'
  """
  if isinstance(raw, bytes):
    return raw.decode("ascii", "replace")
  return str(raw)


def _line_starts_time_sample(
  line: str,
  line_index: int,
  start_idx: int,
) -> bool:
  """
  Return whether ``line`` is a digit sample header at or after ``start_idx``.

  Args:
    line (str): One stats line.
    line_index (int): Zero-based index in the file.
    start_idx (int): First index that may emit.

  Returns:
    bool: True for a timestamp header past the resume index.

  Examples:
    >>> _line_starts_time_sample("1000.0 J h", 1, 0)
    True
  """
  if not line:
    return False
  stripped = line.lstrip()
  if not stripped:
    return False
  return stripped[0].isdigit() and line_index >= start_idx


def iter_codon_flush_line_bundles(
  stats_file: str,
  *,
  flush_max_bytes: int,
  resume_byte: int = 0,
  line_batch_size: int = 4096,
) -> Iterator[list[str]]:
  """
  Yield schema-inclusive line bundles at byte/sample flush boundaries.

  Args:
    stats_file (str): Absolute stats path (already visible under caller lock).
    flush_max_bytes (int): Soft max raw bytes per window.
    resume_byte (int): Seek offset before reading.
    line_batch_size (int): Read batch size.

  Yields:
    list[str]: ``schema_lines + window_lines`` for one flush window.

  Examples:
    >>> list(iter_codon_flush_line_bundles("/missing"))  # doctest: +SKIP
    []
  """
  emission_start = 0
  flush_bytes_n = max(1, int(flush_max_bytes))
  schema_lines: list[str] = []
  window_lines: list[str] = []
  window_sample_n = 0
  line_index = 0
  pending_flush = False

  try:
    with open(stats_file, "rb") as fd:
      if resume_byte > 0:
        fd.seek(int(resume_byte))
      window_start = int(fd.tell())
      while True:
        raw_batch: list[bytes] = []
        for _ in range(int(line_batch_size)):
          raw = fd.readline()
          if not raw:
            break
          raw_batch.append(raw)
        if not raw_batch:
          break
        batch_end = int(fd.tell())
        for raw in raw_batch:
          line = _decode_stats_line(raw)
          over_threshold = (batch_end - window_start) >= flush_bytes_n
          starts = _line_starts_time_sample(line, line_index, emission_start)
          if starts and (pending_flush or over_threshold) and window_lines:
            yield schema_lines + window_lines
            window_start = batch_end
            pending_flush = False
            window_lines = []
            window_sample_n = 0
          if line.startswith("!"):
            schema_lines.append(line)
          else:
            window_lines.append(line)
          if starts:
            window_sample_n += 1
          line_index += 1
          over_after = (batch_end - window_start) >= flush_bytes_n
          if over_after:
            pending_flush = True
    if window_lines:
      yield schema_lines + window_lines
  except FileNotFoundError:
    return
