"""Write Brotli-11 and Gzip-9 sidecars under Django STATIC_ROOT.

After collectstatic and SPA heal, walk the entire STATIC_ROOT tree so
nginx ``brotli_static`` / ``gzip_static`` can serve hashed Next assets
and Django/DRF admin files from on-disk siblings. Keeps uncompressed
originals. Direct ``*.br`` / ``*.gz`` HTTP URLs stay 404 at nginx.

Attributes:
  SIDECAR_COMPRESS_EXTENSIONS: frozenset[str]. Text suffixes nginx may
    serve from ``*.br`` / ``*.gz`` siblings.
  SIDECAR_SKIP_EXTENSIONS: frozenset[str]. Already-encoded or binary
    suffixes that must not get sidecars.
  SIDECAR_MIN_BYTES: int. Match nginx gzip/brotli/zstd ``min_length``.
  _SPA_HTML_PREFIXES: tuple[str, ...]. STATIC_ROOT-relative prefixes
    whose HTML shells heal rewrites; never sidecar those files.
  _DEFAULT_STATIC_ROOT: str. Image/compose STATIC_ROOT when unset.
"""

from __future__ import annotations

import gzip
import os
import sys
from pathlib import Path
from types import ModuleType

SIDECAR_COMPRESS_EXTENSIONS: frozenset[str] = frozenset(
    {".js", ".mjs", ".css", ".svg", ".json", ".txt", ".xml"}
)
SIDECAR_SKIP_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".br",
        ".gz",
        ".zst",
        ".woff",
        ".woff2",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".avif",
        ".gif",
        ".wasm",
        ".map",
    }
)
SIDECAR_MIN_BYTES = 256
_SPA_HTML_PREFIXES: tuple[str, ...] = (
    "frontend/machine/",
    "frontend/pub/",
    "machine/",
    "pub/",
)
_DEFAULT_STATIC_ROOT = "/home/hpcperfstats/staticfiles"


def _load_brotli() -> ModuleType:
  """
  Import the ``brotli`` package or fail closed.

  Returns:
    ModuleType: The ``brotli`` module.

  Raises:
    RuntimeError: Raised when the ``brotli`` package is not installed.

  Examples:
    >>> mod = _load_brotli()
    >>> callable(mod.compress)
    True
  """
  try:
    import brotli
  except ImportError as exc:
    raise RuntimeError(
        "Python brotli package is required to write static sidecars"
    ) from exc
  return brotli


def should_write_static_sidecars(
    rel_posix: str,
    size_bytes: int,
) -> bool:
  """
  Return whether ``rel_posix`` under STATIC_ROOT should get sidecars.

  Args:
    rel_posix (str): Path relative to STATIC_ROOT using posix separators.
    size_bytes (int): Uncompressed file size in bytes.

  Returns:
    bool: True when the file is large enough, not SPA HTML, and has a
    compressible text suffix.

  Examples:
    >>> should_write_static_sidecars("rest_framework/js/api.js", 1024)
    True
    >>> should_write_static_sidecars("frontend/machine/index.html", 4096)
    False
  """
  if size_bytes < SIDECAR_MIN_BYTES:
    return False
  normalized = rel_posix.replace("\\", "/")
  if normalized.endswith(".html") and any(
      normalized.startswith(prefix) for prefix in _SPA_HTML_PREFIXES
  ):
    return False
  ext = Path(normalized).suffix.lower()
  if ext in SIDECAR_SKIP_EXTENSIONS:
    return False
  return ext in SIDECAR_COMPRESS_EXTENSIONS


def _sidecars_are_fresh(source: Path) -> bool:
  """
  Return True when both sidecars exist and are not older than source.

  Args:
    source (Path): Uncompressed static file.

  Returns:
    bool: True when ``source.br`` and ``source.gz`` mtimes are at least
    ``source``'s mtime.

  Examples:
    >>> from pathlib import Path
    >>> _sidecars_are_fresh(Path("/no/such/static.js"))
    False
  """
  br_path = Path(f"{source}.br")
  gz_path = Path(f"{source}.gz")
  if not br_path.is_file() or not gz_path.is_file():
    return False
  src_mtime = source.stat().st_mtime
  return (
      br_path.stat().st_mtime >= src_mtime
      and gz_path.stat().st_mtime >= src_mtime
  )


def compress_static_sidecars(root_dir: str | Path) -> tuple[int, int]:
  """
  Write Brotli-11 and Gzip-9 siblings under ``root_dir``.

  Skips files that already have fresh sidecars (both exist, mtime at
  least the source mtime). Does not delete uncompressed originals.

  Args:
    root_dir (str | Path): Django STATIC_ROOT (or a test tree with the
      same layout).

  Returns:
    tuple[int, int]: ``(written, skipped)`` source-file counts.

  Raises:
    RuntimeError: Raised when the ``brotli`` package is not installed.

  Examples:
    >>> compress_static_sidecars("/no/such/static-root")
    (0, 0)
  """
  brotli_mod = _load_brotli()
  root = Path(root_dir)
  if not root.is_dir():
    return (0, 0)
  written = 0
  skipped = 0
  stack = [root]
  while stack:
    current = stack.pop()
    try:
      entries = list(current.iterdir())
    except OSError:
      continue
    for entry in entries:
      if entry.is_dir():
        stack.append(entry)
        continue
      if not entry.is_file():
        continue
      rel = entry.relative_to(root).as_posix()
      size_bytes = entry.stat().st_size
      if not should_write_static_sidecars(rel, size_bytes):
        skipped += 1
        continue
      if _sidecars_are_fresh(entry):
        skipped += 1
        continue
      raw = entry.read_bytes()
      Path(f"{entry}.br").write_bytes(
          brotli_mod.compress(raw, quality=11)
      )
      Path(f"{entry}.gz").write_bytes(gzip.compress(raw, compresslevel=9))
      written += 1
  return (written, skipped)


def main(argv: list[str] | None = None) -> int:
  """
  Compress STATIC_ROOT from argv or the STATIC_ROOT environment variable.

  Args:
    argv (list[str] | None): CLI args without ``sys.argv[0]``. When
      omitted, uses ``sys.argv[1:]``. First arg overrides STATIC_ROOT.

  Returns:
    int: Process exit code (always 0 on success).

  Raises:
    RuntimeError: Raised when the ``brotli`` package is not installed.

  Examples:
    >>> main(["/no/such/static-root"])
    0
  """
  args = list(sys.argv[1:] if argv is None else argv)
  if args:
    root = args[0]
  else:
    root = os.environ.get("STATIC_ROOT") or _DEFAULT_STATIC_ROOT
  written, skipped = compress_static_sidecars(root)
  print(
      f"STATIC_ROOT sidecars written={written} skipped={skipped} "
      f"root={root}"
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
