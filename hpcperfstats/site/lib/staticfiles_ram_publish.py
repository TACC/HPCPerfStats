"""Atomically publish Django static/media trees onto a RAM dest.

Web startup and ``rebuild_frontend.sh`` mirror finished ``STATIC_ROOT``
and ``MEDIA_ROOT`` onto shared tmpfs mounts so nginx can serve
``/static/`` and ``/media/`` from RAM. Disk trees remain staging;
collectstatic ``--clear`` never touches the ram dests.

Attributes:
  REQUIRED_STATIC_RELPATHS: tuple[str, ...]. SPA shells static publish
    must find on both src and dest with matching sha256.
  DEFAULT_STATIC_RAM_ROOT: str. Compose mount for the static tmpfs.
  DEFAULT_MEDIA_RAM_ROOT: str. Compose mount for the media tmpfs.
  DEFAULT_STATIC_ROOT: str. Image STATIC_ROOT when env is unset.
  DEFAULT_MEDIA_ROOT: str. Image MEDIA_ROOT when env is unset.
  _STAGING_PREFIX: str. Staging directory name prefix on dest.
  _BACKUP_PREFIX: str. Backup directory name prefix on dest.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

REQUIRED_STATIC_RELPATHS: tuple[str, ...] = (
    "frontend/machine/index.html",
    "frontend/pub/index.html",
)
DEFAULT_STATIC_RAM_ROOT = "/home/hpcperfstats/staticfiles-ram"
DEFAULT_MEDIA_RAM_ROOT = "/home/hpcperfstats/media-ram"
DEFAULT_STATIC_ROOT = "/home/hpcperfstats/staticfiles"
DEFAULT_MEDIA_ROOT = (
    "/home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/media"
)
_STAGING_PREFIX = ".publish-"
_BACKUP_PREFIX = ".bak-"


def _fail(message: str) -> None:
  """Print ``message`` to stderr and abort the publish.

  Args:
    message (str): Operator-facing error text.

  Returns:
    None

  Raises:
    SystemExit: Always raised with code 1 after printing.

  Examples:
    >>> try:
    ...     _fail("dest is not writable")
    ... except SystemExit as exc:
    ...     exc.code
    1
  """
  print(message, file=sys.stderr)
  raise SystemExit(1)


def _file_sha256(path: Path) -> str:
  """Return the hex sha256 digest of ``path``.

  Args:
    path (Path): File to hash.

  Returns:
    str: 64-character lowercase hex digest.

  Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     p = Path(tmp) / "a.bin"
    ...     p.write_bytes(b"abc")
    ...     _file_sha256(p) == hashlib.sha256(b"abc").hexdigest()
    True
  """
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def _ignore_staging_names(
    directory: str,
    names: list[str],
) -> list[str]:
  """Return names under ``directory`` that copytree must skip.

  Args:
    directory (str): Directory being copied (unused; shutil contract).
    names (list[str]): Basenames in that directory.

  Returns:
    list[str]: Basenames starting with staging or backup prefixes.

  Examples:
    >>> _ignore_staging_names("/tmp", [".publish-1", "frontend", ".bak-1"])
    ['.publish-1', '.bak-1']
  """
  del directory
  return [
      name
      for name in names
      if name.startswith(_STAGING_PREFIX) or name.startswith(_BACKUP_PREFIX)
  ]


def _is_reserved_dest_name(name: str) -> bool:
  """Return True when ``name`` is a publisher staging or backup dir.

  Args:
    name (str): Basename under the ram dest.

  Returns:
    bool: True for ``.publish-*`` and ``.bak-*`` names.

  Examples:
    >>> _is_reserved_dest_name(".publish-12")
    True
    >>> _is_reserved_dest_name("frontend")
    False
  """
  return name.startswith(_STAGING_PREFIX) or name.startswith(_BACKUP_PREFIX)


def _assert_src_ready(
    src: Path,
    dest: Path,
    required_relpaths: Sequence[str],
) -> None:
  """Fail closed when src/dest are unusable or required files are missing.

  Args:
    src (Path): Staging tree (STATIC_ROOT or MEDIA_ROOT).
    dest (Path): Ram dest on the tmpfs volume.
    required_relpaths (Sequence[str]): Relpaths that must exist as files
      under src before dest is mutated.

  Returns:
    None

  Raises:
    SystemExit: Raised when src is missing, dest equals src, or a
      required file is absent.

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     root = Path(tmp)
    ...     src = root / "src"
    ...     src.mkdir()
    ...     _assert_src_ready(src, root / "dest", ())
  """
  if src.resolve() == dest.resolve():
    _fail(f"ram dest must not equal src: {src}")
  if not src.is_dir():
    _fail(f"publish src is not a directory: {src}")
  missing = [
      rel
      for rel in required_relpaths
      if not (src / rel).is_file()
  ]
  if missing:
    _fail(
        "publish src missing required file(s): " + ", ".join(missing)
    )


def _restore_backup(dest: Path, backup: Path) -> None:
  """Move backup children back onto ``dest`` after a failed swap.

  Args:
    dest (Path): Ram dest that may hold a partial new tree.
    backup (Path): Directory holding the previous dest children.

  Returns:
    None

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     dest = Path(tmp) / "dest"
    ...     backup = Path(tmp) / "bak"
    ...     dest.mkdir()
    ...     backup.mkdir()
    ...     (backup / "keep.txt").write_text("ok", encoding="utf-8")
    ...     _restore_backup(dest, backup)
    ...     (dest / "keep.txt").read_text(encoding="utf-8")
    'ok'
  """
  if not backup.is_dir():
    return
  if not any(backup.iterdir()):
    return
  for child in dest.iterdir():
    if child.name == backup.name or _is_reserved_dest_name(child.name):
      continue
    if child.is_dir() and not child.is_symlink():
      shutil.rmtree(child, ignore_errors=True)
    else:
      child.unlink(missing_ok=True)
  for child in backup.iterdir():
    child.rename(dest / child.name)


def _replace_dest_children(
    dest: Path,
    staging: Path,
    backup: Path,
) -> None:
  """Move dest children aside, then promote staging children into dest.

  Args:
    dest (Path): Ram dest (tmpfs mountpoint; not renamed).
    staging (Path): Complete copy of src living on the same filesystem.
    backup (Path): Empty directory that receives previous dest children.

  Returns:
    None

  Raises:
    OSError: Raised when a rename or mkdir fails (including ENOSPC).

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     dest = Path(tmp) / "dest"
    ...     staging = Path(tmp) / "stage"
    ...     backup = Path(tmp) / "bak"
    ...     dest.mkdir()
    ...     staging.mkdir()
    ...     backup.mkdir()
    ...     (dest / "old.txt").write_text("old", encoding="utf-8")
    ...     (staging / "new.txt").write_text("new", encoding="utf-8")
    ...     _replace_dest_children(dest, staging, backup)
    ...     (dest / "new.txt").read_text(encoding="utf-8")
    'new'
  """
  backup.mkdir(parents=True, exist_ok=True)
  for child in list(dest.iterdir()):
    if child == staging or child == backup:
      continue
    if _is_reserved_dest_name(child.name):
      continue
    child.rename(backup / child.name)
  for child in list(staging.iterdir()):
    child.rename(dest / child.name)


def _verify_required_match(
    src: Path,
    dest: Path,
    required_relpaths: Sequence[str],
) -> None:
  """Fail closed when dest lacks required files or sha256 diverges.

  Args:
    src (Path): Staging tree used as the hash authority.
    dest (Path): Published ram tree.
    required_relpaths (Sequence[str]): Relpaths that must match.

  Returns:
    None

  Raises:
    SystemExit: Raised when a required dest file is missing or hashes
      differ from src.

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     src = Path(tmp) / "src"
    ...     dest = Path(tmp) / "dest"
    ...     src.mkdir()
    ...     dest.mkdir()
    ...     _verify_required_match(src, dest, ())
  """
  for rel in required_relpaths:
    src_file = src / rel
    dest_file = dest / rel
    if not dest_file.is_file():
      _fail(f"publish dest missing required file: {rel}")
    if _file_sha256(src_file) != _file_sha256(dest_file):
      _fail(f"publish dest sha256 mismatch: {rel}")


def publish_tree_to_ram(
    src: Path,
    dest: Path,
    *,
    required_relpaths: Sequence[str] = (),
) -> None:
  """Copy ``src`` onto ``dest`` via same-filesystem staging rename.

  Copies into ``dest/.publish-<pid>``, moves existing dest children into
  a backup dir, promotes the staging children, then verifies required
  relpaths. On failure, restores the backup and exits 1. Empty ``src``
  is valid when ``required_relpaths`` is empty.

  Args:
    src (Path): Staging directory to publish (must exist).
    dest (Path): Ram dest directory on the tmpfs volume.
    required_relpaths (Sequence[str]): Relpaths that must exist as files
      on src before mutation and match dest after the swap.

  Returns:
    None

  Raises:
    SystemExit: Raised when dest equals src, dest is unwritable, src
      lacks required files, tmpfs is full, or post-swap verify fails.

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     src = Path(tmp) / "src"
    ...     dest = Path(tmp) / "dest"
    ...     src.mkdir()
    ...     (src / "hello.txt").write_text("hi", encoding="utf-8")
    ...     publish_tree_to_ram(src, dest)
    ...     (dest / "hello.txt").read_text(encoding="utf-8")
    'hi'
  """
  src = Path(src)
  dest = Path(dest)
  _assert_src_ready(src, dest, required_relpaths)
  dest.mkdir(parents=True, exist_ok=True)
  if not dest.is_dir() or not os.access(dest, os.W_OK | os.X_OK):
    _fail(f"publish dest is not a writable directory: {dest}")
  pid = os.getpid()
  staging = dest / f"{_STAGING_PREFIX}{pid}"
  backup = dest / f"{_BACKUP_PREFIX}{pid}"
  if staging.exists():
    shutil.rmtree(staging)
  if backup.exists():
    shutil.rmtree(backup)
  try:
    shutil.copytree(
        src,
        staging,
        ignore=_ignore_staging_names,
    )
    _replace_dest_children(dest, staging, backup)
    _verify_required_match(src, dest, required_relpaths)
  except SystemExit as exc:
    _restore_backup(dest, backup)
    if staging.exists():
      shutil.rmtree(staging, ignore_errors=True)
    raise SystemExit(exc.code) from exc
  except OSError as exc:
    _restore_backup(dest, backup)
    if staging.exists():
      shutil.rmtree(staging, ignore_errors=True)
    _fail(f"publish to ram failed: {exc}")
  else:
    if backup.exists():
      shutil.rmtree(backup, ignore_errors=True)
    if staging.exists():
      shutil.rmtree(staging, ignore_errors=True)


def _paths_for_kind(
    kind: str,
) -> tuple[Path, Path, tuple[str, ...]]:
  """Return src, dest, and required relpaths for ``kind``.

  Args:
    kind (str): ``static`` or ``media``.

  Returns:
    tuple[Path, Path, tuple[str, ...]]: Staging src, ram dest, and
      required relpaths (SPA shells for static; empty for media).

  Raises:
    SystemExit: Raised when ``kind`` is not ``static`` or ``media``.

  Examples:
    >>> import os
    >>> os.environ.pop("STATIC_ROOT", None)
    >>> os.environ.pop("STATICFILES_RAM_ROOT", None)
    >>> src, dest, required = _paths_for_kind("static")
    >>> src.as_posix() == DEFAULT_STATIC_ROOT
    True
    >>> dest.as_posix() == DEFAULT_STATIC_RAM_ROOT
    True
    >>> required == REQUIRED_STATIC_RELPATHS
    True
  """
  if kind == "static":
    src = Path(os.environ.get("STATIC_ROOT") or DEFAULT_STATIC_ROOT)
    dest = Path(
        os.environ.get("STATICFILES_RAM_ROOT") or DEFAULT_STATIC_RAM_ROOT
    )
    return src, dest, REQUIRED_STATIC_RELPATHS
  if kind == "media":
    src = Path(os.environ.get("MEDIA_ROOT") or DEFAULT_MEDIA_ROOT)
    dest = Path(
        os.environ.get("MEDIAFILES_RAM_ROOT") or DEFAULT_MEDIA_RAM_ROOT
    )
    return src, dest, ()
  _fail(f"unknown publish kind: {kind}")
  raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
  """Publish ``--kind static`` or ``--kind media`` using env path defaults.

  Args:
    argv (list[str] | None): CLI args without ``sys.argv[0]``. When
      omitted, uses ``sys.argv[1:]``.

  Returns:
    int: Process exit code (0 on success).

  Raises:
    SystemExit: Raised by argparse on bad flags, or by publish when the
      tree cannot be mirrored.

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     src = Path(tmp) / "media"
    ...     dest = Path(tmp) / "media-ram"
    ...     src.mkdir()
    ...     import os
    ...     os.environ["MEDIA_ROOT"] = str(src)
    ...     os.environ["MEDIAFILES_RAM_ROOT"] = str(dest)
    ...     main(["--kind", "media"])
    0
  """
  parser = argparse.ArgumentParser(
      description="Publish STATIC_ROOT or MEDIA_ROOT onto a ram dest.",
  )
  parser.add_argument(
      "--kind",
      choices=("static", "media"),
      required=True,
      help="static requires SPA shells; media may be empty.",
  )
  args = parser.parse_args(sys.argv[1:] if argv is None else argv)
  src, dest, required = _paths_for_kind(args.kind)
  publish_tree_to_ram(src, dest, required_relpaths=required)
  print(f"published {args.kind} {src} -> {dest}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
