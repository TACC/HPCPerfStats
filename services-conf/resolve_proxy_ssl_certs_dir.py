#!/usr/bin/env python3
"""
Materialize proxy TLS PEMs from a read-only settings mount into nginx paths.

Reads ``fullchain.pem`` and ``privkey.pem`` from ``--ssl-source-mount`` (typically
``/mnt/ssl-source`` from the ``proxy_ssl_source`` Compose volume). Optional
``--ssl-certs-rel`` selects a subpath for Let's Encrypt ``live/hostname`` layouts.

Attributes:
  REQUIRED_PEM_NAMES: Basename tuple that must exist under the source directory.
  FIXTURE_REL: Checkout-relative path of the committed self-signed fixture.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

REQUIRED_PEM_NAMES: tuple[str, ...] = ("fullchain.pem", "privkey.pem")
FIXTURE_REL: str = "tests/fixtures/proxy-ssl"


def services_conf_dir() -> Path:
  """
  Return the directory that contains this script (``services-conf/``).

  Returns:
    Path: Absolute path of ``services-conf/``.

  Examples:
    >>> services_conf_dir().name
    'services-conf'
  """
  return Path(__file__).resolve().parent


def repo_root() -> Path:
  """
  Return the git checkout root (parent of ``services-conf/``).

  Returns:
    Path: Absolute path of the HPCPerfStats checkout.

  Examples:
    >>> (repo_root() / "services-conf").is_dir()
    True
  """
  return services_conf_dir().parent


def fixture_ssl_certs_dir() -> Path:
  """
  Return the absolute path of the committed proxy TLS fixture directory.

  Returns:
    Path: Absolute ``tests/fixtures/proxy-ssl`` path.

  Examples:
    >>> fixture_ssl_certs_dir().name
    'proxy-ssl'
  """
  return (repo_root() / FIXTURE_REL).resolve()


def apply_stat_ownership_and_mode(path: Path, st: os.stat_result) -> None:
  """
  Apply *st* mode bits and uid/gid to *path* (best-effort for ownership).

  Args:
    path (Path): File or directory to update.
    st (os.stat_result): Source ``stat`` result to mirror.

  Returns:
    None: This function mutates *path* in place.

  Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> p = Path(tempfile.mkdtemp()) / "f"
    >>> _ = p.write_text("x", encoding="utf-8")
    >>> apply_stat_ownership_and_mode(p, p.stat())
  """
  os.chmod(path, stat.S_IMODE(st.st_mode))
  try:
    os.chown(path, st.st_uid, st.st_gid)
  except PermissionError:
    pass


def assert_path_under_mount(path: Path, mount_root: Path) -> None:
  """
  Require *path* to resolve inside *mount_root*.

  Args:
    path (Path): Candidate path (may be a symlink target).
    mount_root (Path): Absolute resolved mount root.

  Raises:
    ValueError: When *path* escapes *mount_root*.

  Returns:
    None: This function validates in place.

  Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> td = Path(tempfile.mkdtemp())
    >>> root = (td / "mount").resolve()
    >>> root.mkdir()
    >>> inner = root / "live" / "host"
    >>> inner.mkdir(parents=True)
    >>> assert_path_under_mount(inner.resolve(), root)
  """
  resolved_mount = mount_root.resolve()
  resolved_path = path.resolve()
  try:
    resolved_path.relative_to(resolved_mount)
  except ValueError as exc:
    raise ValueError(
        f"PEM path {resolved_path} is outside ssl source mount {resolved_mount}; "
        "widen proxy_ssl_source.device to include archive symlinks"
    ) from exc


def resolve_source_certs_dir(
    ssl_source_mount: Path,
    ssl_certs_rel: str | None = None,
) -> Path:
  """
  Join the settings mount with an optional relative certificate subpath.

  Args:
    ssl_source_mount (Path): Read-only mount root (for example ``/mnt/ssl-source``).
    ssl_certs_rel (str | None): Optional subpath under the mount for LE ``live/…``.

  Returns:
    Path: Absolute resolved source directory containing PEMs.

  Raises:
    ValueError: When the joined path escapes the mount or is not a directory.

  Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> td = Path(tempfile.mkdtemp())
    >>> mount = td / "mount"
    >>> mount.mkdir()
    >>> (mount / "fullchain.pem").write_text("c", encoding="utf-8")
    >>> (mount / "privkey.pem").write_text("k", encoding="utf-8")
    >>> resolve_source_certs_dir(mount) == mount.resolve()
    True
  """
  mount_root = ssl_source_mount.expanduser().resolve()
  if not mount_root.is_dir():
    raise ValueError(f"ssl source mount is not a directory: {mount_root}")
  rel = (ssl_certs_rel or "").strip().strip("/")
  if rel:
    candidate = (mount_root / rel).resolve()
    assert_path_under_mount(candidate, mount_root)
  else:
    candidate = mount_root
  if not candidate.is_dir():
    raise ValueError(f"ssl certs source is not a directory: {candidate}")
  return candidate


def resolve_readable_pem(
    certs_dir: Path,
    name: str,
    *,
    mount_root: Path | None = None,
) -> Path:
  """
  Resolve *name* under *certs_dir* to a readable regular file.

  Follows Let's Encrypt ``live/`` symlinks into ``archive/`` when *mount_root*
  is set, the resolved target must remain under *mount_root*.

  Args:
    certs_dir (Path): Absolute certificate directory.
    name (str): Basename such as ``fullchain.pem``.
    mount_root (Path | None): When set, resolved targets must stay under this
      mount root.

  Returns:
    Path: Absolute path of the readable PEM file (symlink target when linked).

  Raises:
    ValueError: When the path is missing, a broken symlink, not a file, not
      readable, or escapes *mount_root*.

  Examples:
    >>> resolve_readable_pem(fixture_ssl_certs_dir(), "fullchain.pem").is_file()
    True
  """
  link = certs_dir / name
  try:
    real = link.resolve(strict=True)
  except FileNotFoundError as exc:
    if link.is_symlink():
      raise ValueError(
          f"{name} is a broken symlink: {link} -> {os.readlink(link)}"
      ) from exc
    raise ValueError(f"missing {name} under {certs_dir}") from exc
  if mount_root is not None:
    assert_path_under_mount(real, mount_root)
  if not real.is_file():
    raise ValueError(f"{name} resolves to a non-file: {link} -> {real}")
  if not os.access(real, os.R_OK):
    raise ValueError(f"{name} is not readable: {real}")
  return real


def validate_ssl_certs_dir(
    certs_dir: Path,
    *,
    mount_root: Path | None = None,
) -> Path:
  """
  Require *certs_dir* to be a directory containing readable required PEMs.

  Args:
    certs_dir (Path): Candidate directory (may be relative).
    mount_root (Path | None): When set, PEM symlink targets must stay under this
      mount root.

  Returns:
    Path: Absolute resolved directory path.

  Raises:
    ValueError: When the path is missing, not a directory, or lacks readable
      ``fullchain.pem`` / ``privkey.pem``.

  Examples:
    >>> d = fixture_ssl_certs_dir()
    >>> validate_ssl_certs_dir(d) == d
    True
  """
  resolved = certs_dir.expanduser().resolve()
  if not resolved.is_dir():
    raise ValueError(f"ssl certs source is not a directory: {resolved}")
  errors: list[str] = []
  for name in REQUIRED_PEM_NAMES:
    try:
      resolve_readable_pem(resolved, name, mount_root=mount_root)
    except ValueError as exc:
      errors.append(str(exc))
  if errors:
    listing = sorted(resolved.iterdir()) if os.access(resolved, os.R_OK) else []
    names = ", ".join(p.name for p in listing) or "(unreadable or empty)"
    raise ValueError(
        f"ssl certs source {resolved} PEM check failed: "
        + "; ".join(errors)
        + f"; directory entries: {names}"
    )
  return resolved


def copy_pems_preserving_meta(
    certs_dir: Path,
    dest_dir: Path,
    *,
    mount_root: Path | None = None,
) -> Path:
  """
  Copy required PEMs into *dest_dir*, preserving source file and dir metadata.

  Materialized destination files are regular files, not symlinks.

  Args:
    certs_dir (Path): Validated source directory.
    dest_dir (Path): Destination directory (created if needed).
    mount_root (Path | None): When set, PEM symlink targets must stay under this
      mount root.

  Returns:
    Path: *dest_dir* after PEMs are copied.

  Raises:
    ValueError: When a required PEM cannot be resolved.
    OSError: When copies fail.

  Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> fix = fixture_ssl_certs_dir()
    >>> td = Path(tempfile.mkdtemp()) / "out"
    >>> copy_pems_preserving_meta(fix, td)
    >>> (td / "fullchain.pem").is_file()
    True
  """
  validated = validate_ssl_certs_dir(certs_dir, mount_root=mount_root)
  dest_dir.mkdir(parents=True, exist_ok=True)
  apply_stat_ownership_and_mode(dest_dir, validated.stat())
  for name in REQUIRED_PEM_NAMES:
    src = resolve_readable_pem(validated, name, mount_root=mount_root)
    src_st = src.stat()
    out = dest_dir / name
    if out.exists() or out.is_symlink():
      out.unlink()
    shutil.copy2(src, out)
    apply_stat_ownership_and_mode(out, src_st)
  return dest_dir


def materialize_ssl_certs(
    ssl_source_mount: Path,
    dest_dir: Path,
    *,
    ssl_certs_rel: str | None = None,
) -> Path:
  """
  Copy PEMs from the settings mount into the nginx certificate directory.

  Args:
    ssl_source_mount (Path): Read-only mount root (for example ``/mnt/ssl-source``).
    dest_dir (Path): Destination directory (for example ``/etc/ssl/hpcperfstats``).
    ssl_certs_rel (str | None): Optional subpath under the mount for LE layouts.

  Returns:
    Path: Absolute *dest_dir* after materialization.

  Raises:
    ValueError: When the source path is invalid or PEMs are missing.

  Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> fix = fixture_ssl_certs_dir()
    >>> td = Path(tempfile.mkdtemp())
    >>> dest = td / "nginx-ssl"
    >>> materialize_ssl_certs(fix, dest) == dest.resolve()
    True
  """
  mount_root = ssl_source_mount.expanduser().resolve()
  source = resolve_source_certs_dir(mount_root, ssl_certs_rel)
  return copy_pems_preserving_meta(
      source,
      dest_dir,
      mount_root=mount_root,
  )


def main(argv: list[str] | None = None) -> int:
  """
  Materialize TLS PEMs from a settings mount into ``--dest-dir``.

  Args:
    argv (list[str] | None): CLI arguments without the program name, or
      ``None`` to use ``sys.argv[1:]``.

  Returns:
    int: ``0`` on success; ``1`` when validation or materialization fails.

  Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> fix = fixture_ssl_certs_dir()
    >>> td = Path(tempfile.mkdtemp())
    >>> dest = td / "out"
    >>> main(["--ssl-source-mount", str(fix), "--dest-dir", str(dest)])
    0
  """
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--ssl-source-mount",
      type=Path,
      default=None,
      help="read-only TLS source mount (e.g. /mnt/ssl-source)",
  )
  parser.add_argument(
      "--ssl-certs-rel",
      default=None,
      help="optional subpath under the mount (LE live/hostname)",
  )
  parser.add_argument(
      "--dest-dir",
      type=Path,
      required=True,
      help="destination directory for materialized PEMs",
  )
  parser.add_argument(
      "--fixture",
      action="store_true",
      help="use committed tests/fixtures/proxy-ssl as --ssl-source-mount",
  )
  args = parser.parse_args(argv)
  if not args.fixture and args.ssl_source_mount is None:
    parser.error("--ssl-source-mount is required unless --fixture is set")
  try:
    mount = (
        fixture_ssl_certs_dir()
        if args.fixture
        else args.ssl_source_mount
    )
    dest = materialize_ssl_certs(
        mount,
        args.dest_dir,
        ssl_certs_rel=args.ssl_certs_rel,
    )
  except ValueError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1
  print(dest, file=sys.stderr)
  return 0


if __name__ == "__main__":
  sys.exit(main())
