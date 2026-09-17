#!/usr/bin/env python3
"""
Derive deterministic sync_timedb benchmark raw stats from immutable exemplars.

Rewrites virtual hostname and sample epochs while preserving schema payloads,
counter spacing, and source file bytes (sources are hashed before/after and
must remain unchanged). Writes a JSON manifest under the output directory.

Attributes:
  MANIFEST_VERSION: Manifest schema version written to ``manifest.json``.
  _EPOCH_LINE_RE: Regex matching digit-leading monitor sample header lines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

MANIFEST_VERSION = 1
_EPOCH_LINE_RE = re.compile(
    r"^(\s*)(\d+(?:\.\d+)?)(\s+)(\S+)(\s+)(\S+)(.*)$",
)


def _sha256_bytes(data: bytes) -> str:
  """
  Return the SHA-256 hex digest for ``data``.

  Args:
    data (bytes): Raw bytes to hash.

  Returns:
    str: Lowercase hex SHA-256 digest.

  Examples:
    >>> _sha256_bytes(b"abc")
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
  """
  return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
  """
  Stream ``path`` and return its SHA-256 hex digest.

  Args:
    path (Path): Existing regular file to hash.

  Returns:
    str: Lowercase hex SHA-256 digest.

  Examples:
    >>> _sha256_file(Path("/nonexistent"))  # doctest: +SKIP
  """
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def _parse_original_host(text: str) -> str:
  """
  Read the source hostname from ``$hostname`` or the first sample line.

  Args:
    text (str): Full raw stats file body.

  Returns:
    str: Hostname token from the file header or first epoch line.

  Raises:
    ValueError: When no hostname can be parsed.

  Examples:
    >>> _parse_original_host("$hostname cn001\\n1000.0 j cn001\\n")
    'cn001'
  """
  for line in text.splitlines():
    stripped = line.lstrip()
    if stripped.startswith("$hostname "):
      host = stripped.split(None, 1)[1].strip()
      if host:
        return host
    if stripped.startswith("$host "):
      host = stripped.split(None, 1)[1].strip()
      if host:
        return host
  for line in text.splitlines():
    match = _EPOCH_LINE_RE.match(line)
    if match is not None:
      host = match.group(6)
      if host:
        return host
  raise ValueError("stats text missing $hostname and digit-leading sample line")


def _epoch_bounds(text: str) -> tuple[float, float]:
  """
  Return min/max epoch values from digit-leading sample lines.

  Args:
    text (str): Full raw stats file body.

  Returns:
    tuple[float, float]: Inclusive ``(min_epoch, max_epoch)`` bounds.

  Raises:
    ValueError: When the file contains no digit-leading sample lines.

  Examples:
    >>> _epoch_bounds("1000.0 j h\\n1010.5 j h\\n")
    (1000.0, 1010.5)
  """
  epochs: list[float] = []
  for line in text.splitlines():
    match = _EPOCH_LINE_RE.match(line)
    if match is None:
      continue
    epochs.append(float(match.group(2)))
  if not epochs:
    raise ValueError("stats text missing digit-leading sample lines")
  return min(epochs), max(epochs)


def _shift_epoch_token(token: str, epoch_offset: int) -> str:
  """
  Add ``epoch_offset`` to a sample epoch token preserving fractional width.

  Args:
    token (str): Leading epoch token from a digit-leading sample line.
    epoch_offset (int): Seconds to add to the parsed epoch value.

  Returns:
    str: Shifted epoch token with the source fractional precision preserved.

  Examples:
    >>> _shift_epoch_token("1000.0", 10)
    '1010.0'
    >>> _shift_epoch_token("1000.123456", 1)
    '1001.123456'
  """
  original = float(token)
  shifted = original + epoch_offset
  if "." in token:
    frac_digits = len(token.split(".", 1)[1])
    formatted = f"{shifted:.{frac_digits}f}"
    return formatted
  if shifted.is_integer():
    return str(int(shifted))
  return str(shifted)


def rewrite_stats_identity(
    text: str,
    *,
    new_host: str,
    epoch_offset: int,
) -> str:
  """
  Rewrite hostname and sample epochs without touching schema/metric payloads.

  Args:
    text (str): Source raw stats file contents.
    new_host (str): Synthetic hostname for ``$hostname`` and sample lines.
    epoch_offset (int): Fixed seconds added to every digit-leading epoch token.

  Returns:
    str: Derived stats text with rewritten identity fields only.

  Examples:
    >>> rewrite_stats_identity(
    ...     "$hostname cn001\\n1000.0 j cn001\\n",
    ...     new_host="benchhost0000",
    ...     epoch_offset=10,
    ... )
    '$hostname benchhost0000\\n1010.0 j benchhost0000\\n'
  """
  out_lines: list[str] = []
  for line in text.splitlines(keepends=True):
    stripped = line.lstrip()
    if stripped.startswith("$hostname ") or stripped.startswith("$host "):
      prefix = line[: len(line) - len(stripped)]
      key = stripped.split(None, 1)[0]
      out_lines.append(f"{prefix}{key} {new_host}\n")
      continue
    match = _EPOCH_LINE_RE.match(line.rstrip("\n"))
    if match is not None:
      shifted = _shift_epoch_token(match.group(2), epoch_offset)
      newline = "\n" if line.endswith("\n") else ""
      rebuilt = (
          f"{match.group(1)}{shifted}{match.group(3)}{match.group(4)}"
          f"{match.group(5)}{new_host}{match.group(7)}{newline}"
      )
      out_lines.append(rebuilt)
      continue
    out_lines.append(line)
  return "".join(out_lines)


def _assert_no_host_epoch_overlap(
    ranges: Sequence[tuple[str, float, float]],
) -> None:
  """
  Reject overlapping epoch ranges registered for the same derived host.

  Args:
    ranges (Sequence[tuple[str, float, float]]): ``(host, min_epoch,
      max_epoch)`` tuples for each derived output.

  Returns:
    None

  Raises:
    ValueError: When two ranges for one host overlap.

  Examples:
    >>> _assert_no_host_epoch_overlap([("h", 1.0, 2.0), ("h", 3.0, 4.0)])
    >>> _assert_no_host_epoch_overlap([("h", 1.0, 3.0), ("h", 2.0, 4.0)])
    Traceback (most recent call last):
    ...
    ValueError: ...
  """
  by_host: dict[str, list[tuple[float, float]]] = {}
  for host, lo, hi in ranges:
    for existing_lo, existing_hi in by_host.get(host, []):
      if not (hi < existing_lo or existing_hi < lo):
        raise ValueError(
            "derived host %r epoch ranges overlap: "
            "[%s, %s] vs [%s, %s]"
            % (host, lo, hi, existing_lo, existing_hi),
        )
    by_host.setdefault(host, []).append((lo, hi))


def select_smallest_sources(
    source_paths: Sequence[str | Path],
    max_files: int | None,
) -> list[Path]:
  """
  Return ``source_paths`` ordered by size, optionally truncated.

  Args:
    source_paths (Sequence[str | Path]): Candidate exemplar raw stats paths.
    max_files (int | None): When set and positive, keep only the ``max_files``
      smallest regular files. ``None`` or non-positive keeps all paths sorted
      by ascending size then path.

  Returns:
    list[Path]: Resolved paths ordered by ascending size.

  Raises:
    ValueError: When a path is not a regular file.

  Examples:
    >>> select_smallest_sources([], 2)
    []
  """
  resolved: list[Path] = []
  for path in source_paths:
    candidate = Path(path).resolve()
    if not candidate.is_file():
      raise ValueError("source path is not a regular file: %s" % candidate)
    resolved.append(candidate)
  ordered = sorted(resolved, key=lambda path: (path.stat().st_size, str(path)))
  if max_files is None or max_files <= 0:
    return ordered
  return ordered[: int(max_files)]


def derive_corpus(
    source_paths: Sequence[str | Path],
    output_dir: str | Path,
    *,
    host_prefix: str = "benchhost",
    host_suffix: str = "",
    epoch_base_offset: int = 1_700_000_000,
    dry_run: bool = False,
) -> dict[str, Any]:
  """
  Derive benchmark raw stats copies and return a manifest describing them.

  Each source file receives a unique synthetic host and a fixed per-file epoch
  offset large enough to keep shifted ranges disjoint across outputs. Source
  files are hashed before and after processing and are never modified.

  Args:
    source_paths (Sequence[str | Path]): Immutable exemplar raw stats paths.
    output_dir (str | Path): Directory receiving derived host/epoch tree copies.
    host_prefix (str): Prefix for synthetic hostnames (suffix is zero-padded
      index).
    host_suffix (str): Optional FQDN suffix appended after the zero-padded
      index (for example ``.cluster_name.domain.edu`` so archive dirs end with
      ``host_name_ext``).
    epoch_base_offset (int): Base epoch for the first derived file; later files
      are placed in non-overlapping slots after the widest source span.
    dry_run (bool): When ``True``, compute the manifest without writing outputs.

  Returns:
    dict[str, Any]: Manifest with ``entries`` listing source/output hashes and
      identity rewrites.

  Raises:
    ValueError: When inputs are empty, parsing fails, or host/epoch ranges
      would collide.

  Examples:
    >>> derive_corpus([], "/tmp/out")  # doctest: +SKIP
  """
  if not source_paths:
    raise ValueError("source_paths must not be empty")
  normalized_sources = [Path(path).resolve() for path in source_paths]
  for path in normalized_sources:
    if not path.is_file():
      raise ValueError("source path is not a regular file: %s" % path)

  output_root = Path(output_dir)
  if not dry_run:
    output_root.mkdir(parents=True, exist_ok=True)

  prepared: list[dict[str, Any]] = []
  max_span = 0.0
  for index, source_path in enumerate(normalized_sources):
    text = source_path.read_text(encoding="utf-8", errors="surrogateescape")
    original_host = _parse_original_host(text)
    epoch_min, epoch_max = _epoch_bounds(text)
    span = epoch_max - epoch_min
    if span > max_span:
      max_span = span
    prepared.append(
        {
            "index": index,
            "source_path": source_path,
            "text": text,
            "original_host": original_host,
            "epoch_min": epoch_min,
            "epoch_max": epoch_max,
        },
    )

  slot = max(max_span + 1.0, 1.0)
  entries: list[dict[str, Any]] = []
  host_ranges: list[tuple[str, float, float]] = []
  suffix = str(host_suffix or "")

  for item in prepared:
    index = item["index"]
    source_path = item["source_path"]
    source_before = _sha256_file(source_path)
    derived_host = "%s%04d%s" % (host_prefix, index, suffix)
    target_start = epoch_base_offset + index * slot
    epoch_offset = int(round(target_start - item["epoch_min"]))
    derived_text = rewrite_stats_identity(
        item["text"],
        new_host=derived_host,
        epoch_offset=epoch_offset,
    )
    derived_min, derived_max = _epoch_bounds(derived_text)
    host_ranges.append((derived_host, derived_min, derived_max))
    shifted_first_epoch = int(derived_min)
    output_path = output_root / derived_host / str(shifted_first_epoch)
    entry = {
        "source_path": str(source_path),
        "source_sha256_before": source_before,
        "output_path": str(output_path),
        "original_host": item["original_host"],
        "derived_host": derived_host,
        "epoch_offset": epoch_offset,
        "epoch_min": derived_min,
        "epoch_max": derived_max,
        "shifted_first_epoch": shifted_first_epoch,
    }
    if not dry_run:
      output_path.parent.mkdir(parents=True, exist_ok=True)
      output_path.write_text(derived_text, encoding="utf-8", errors="surrogateescape")
      entry["output_sha256"] = _sha256_file(output_path)
    else:
      entry["output_sha256"] = _sha256_bytes(
          derived_text.encode("utf-8", errors="surrogateescape"),
      )
    source_after = _sha256_file(source_path)
    entry["source_sha256_after"] = source_after
    entries.append(entry)

  _assert_no_host_epoch_overlap(host_ranges)

  manifest: dict[str, Any] = {
      "version": MANIFEST_VERSION,
      "host_prefix": host_prefix,
      "host_suffix": suffix,
      "epoch_base_offset": epoch_base_offset,
      "epoch_slot_seconds": slot,
      "dry_run": dry_run,
      "entries": entries,
  }
  if not dry_run:
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest["manifest_path"] = str(manifest_path)
  return manifest


def verify_source_unchanged(manifest: dict[str, Any]) -> None:
  """
  Ensure manifest-recorded sources still match their before/after digests.

  Args:
    manifest (dict[str, Any]): Manifest returned by :func:`derive_corpus`.

  Returns:
    None

  Raises:
    ValueError: When recorded digests disagree or on-disk sources drift.

  Examples:
    >>> verify_source_unchanged({"entries": []})
  """
  entries = manifest.get("entries")
  if not isinstance(entries, list):
    raise ValueError("manifest missing entries list")
  for entry in entries:
    if not isinstance(entry, dict):
      raise ValueError("manifest entry is not an object")
    before = entry.get("source_sha256_before")
    after = entry.get("source_sha256_after")
    source_path = entry.get("source_path")
    if not before or not after:
      raise ValueError("manifest entry missing source sha256 fields: %r" % entry)
    if before != after:
      raise ValueError(
          "source hash drift recorded in manifest for %r" % source_path,
      )
    if source_path:
      path = Path(source_path)
      if path.is_file():
        current = _sha256_file(path)
        if current != before:
          raise ValueError(
              "source file changed on disk: %r" % source_path,
          )


def _collect_source_paths(source_dirs: Iterable[str | Path]) -> list[Path]:
  """
  Recursively collect raw stats files under each source directory.

  Args:
    source_dirs (Iterable[str | Path]): Roots containing exemplar raw stats
      trees (for example ``archive/host/epoch`` leaves).

  Returns:
    list[Path]: Sorted absolute paths to regular files, excluding lock sidecars.

  Raises:
    ValueError: When a source directory does not exist.

  Examples:
    >>> _collect_source_paths([])  # doctest: +SKIP
  """
  collected: list[Path] = []
  for root in source_dirs:
    base = Path(root)
    if not base.is_dir():
      raise ValueError("source directory does not exist: %s" % base)
    for path in sorted(base.rglob("*")):
      if not path.is_file():
        continue
      if path.name.endswith(".fnctl.lock"):
        continue
      if path.name.startswith("."):
        continue
      collected.append(path.resolve())
  return sorted(collected)


def _build_arg_parser() -> argparse.ArgumentParser:
  """
  Build the CLI argument parser for corpus derivation.

  Returns:
    argparse.ArgumentParser: Parser for ``--source-dir`` and ``--output-dir``.

  Examples:
    >>> _build_arg_parser().prog  # doctest: +SKIP
  """
  parser = argparse.ArgumentParser(
      description=(
          "Derive deterministic sync_timedb benchmark raw stats from exemplars."
      ),
  )
  parser.add_argument(
      "--source-dir",
      action="append",
      default=[],
      required=True,
      help="Directory containing immutable exemplar raw stats (repeatable).",
  )
  parser.add_argument(
      "--output-dir",
      required=True,
      help="Directory for derived host/epoch tree and manifest.json.",
  )
  parser.add_argument(
      "--host-prefix",
      default="benchhost",
      help="Synthetic hostname prefix (default: benchhost).",
  )
  parser.add_argument(
      "--host-suffix",
      default="",
      help=(
          "Optional FQDN suffix after the padded index "
          "(example: .cluster_name.domain.edu)."
      ),
  )
  parser.add_argument(
      "--max-files",
      type=int,
      default=0,
      help=(
          "When >0, derive only the N smallest source files "
          "(smoke/steady tier selection)."
      ),
  )
  parser.add_argument(
      "--epoch-base-offset",
      type=int,
      default=1_700_000_000,
      help="Base epoch for the first derived file (default: 1700000000).",
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      help="Compute manifest only; do not write derived files.",
  )
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry point for benchmark corpus derivation.

  Args:
    argv (Sequence[str] | None): Optional argument vector (defaults to
      ``sys.argv[1:]``).

  Returns:
    int: ``0`` on success, ``1`` on user error.

  Raises:
    SystemExit: When argparse exits for ``--help`` or invalid CLI flags.
    ValueError: Propagated only if not caught; user errors are caught and
      returned as exit code ``1``.

  Examples:
    >>> main(["--help"])  # doctest: +SKIP
  """
  parser = _build_arg_parser()
  args = parser.parse_args(list(argv) if argv is not None else None)
  try:
    source_paths = _collect_source_paths(args.source_dir)
    if not source_paths:
      raise ValueError("no source files found under --source-dir paths")
    source_paths = select_smallest_sources(source_paths, args.max_files)
    if not source_paths:
      raise ValueError("no source files remain after --max-files selection")
    derive_corpus(
        source_paths,
        args.output_dir,
        host_prefix=args.host_prefix,
        host_suffix=args.host_suffix,
        epoch_base_offset=args.epoch_base_offset,
        dry_run=args.dry_run,
    )
  except ValueError as exc:
    print("error: %s" % exc, file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
