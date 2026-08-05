"""
Ensure Next SPA shells exist under STATIC_ROOT/frontend after collectstatic.

Persistent ``staticfiles_data`` volumes can retain a Vite-era ``frontend/`` tree
where ``collectstatic`` reports unmodified files and never materializes
``machine/index.html`` / ``pub/index.html``. When the image package static still
has those shells, replace ``STATIC_ROOT/frontend`` from package static.

Volumes can also retain an older Next export whose shells exist but whose
``machine/index.html`` (and hashed chunks) differ from the image package after a
from-scratch Docker rebuild. Compare sha256 fingerprints and replace on drift.

Attributes:
  REQUIRED_SPA_SHELLS: Attribute.
  _MACHINE_SHELL: Attribute.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

REQUIRED_SPA_SHELLS: tuple[str, ...] = ("machine/index.html", "pub/index.html")
_MACHINE_SHELL = "machine/index.html"


def purge_nginx_config_from_public_frontend(
  frontend_root: str | Path,
  *,
  out: TextIO | None = None,
) -> list[str]:
  """
  Delete nginx/config leftovers under the public SPA static tree.

  Args:
    frontend_root (str | Path): ``STATIC_ROOT/frontend`` (or package static
      frontend) that nginx may HTTP-serve.
    out (TextIO | None): Optional log stream for removed paths.

  Returns:
    list[str]: Relative paths removed (posix), sorted.

  Examples:
    >>> purge_nginx_config_from_public_frontend("/no/such/frontend")
    []
  """
  root = Path(frontend_root)
  if not root.is_dir():
    return []
  removed: list[str] = []
  patterns = (
    "*.inc",
    "*.md",
    "*.markdown",
    "*.map",
    "*.example",
    "*.sh",
    "*.py",
    "*.toml",
    "*.ini",
    "*.yml",
    "*.yaml",
  )
  for pattern in patterns:
    for path in root.rglob(pattern):
      if not path.is_file():
        continue
      rel = path.relative_to(root).as_posix()
      path.unlink(missing_ok=True)
      removed.append(rel)
  removed = sorted(set(removed))
  if removed and out is not None:
    print(
      "Purged non-web leftovers from public frontend static: " + ", ".join(removed),
      file=out,
    )
  return removed


def missing_required_shells(
  frontend_root: str | Path,
  required: Sequence[str] = REQUIRED_SPA_SHELLS,
) -> list[str]:
  """
  Missing required shells.
  
  Args:
    frontend_root (str | Path): One of ``str``, ``Path``.
    required (Sequence[str]): Sequence for required.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> missing_required_shells("x", [])  # doctest: +SKIP
  """
  root = Path(frontend_root)
  return [str(root / rel) for rel in required if not (root / rel).is_file()]


def package_has_required_shells(
  package_frontend: str | Path,
  required: Sequence[str] = REQUIRED_SPA_SHELLS,
) -> bool:
  """
  Package has required shells.
  
  Args:
    package_frontend (str | Path): One of ``str``, ``Path``.
    required (Sequence[str]): Sequence for required.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> package_has_required_shells("x", [])  # doctest: +SKIP
  """
  return not missing_required_shells(package_frontend, required)


def spa_shell_fingerprint(frontend_root: str | Path) -> str:
  """
  Return sha256 hex of ``machine/index.html``, or ``""`` if missing.
  
  Args:
    frontend_root (str | Path): One of ``str``, ``Path``.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> spa_shell_fingerprint("x")  # doctest: +SKIP
  """
  path = Path(frontend_root) / _MACHINE_SHELL
  if not path.is_file():
    return ""
  return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_package_frontend_dir(
  *,
  staticfiles_dirs: Sequence[str | Path] | None = None,
  settings_dir: str | Path | None = None,
) -> Path:
  """
  Locate package ``…/static/frontend`` from Django settings paths.
  
  Args:
    staticfiles_dirs (Sequence[str | Path] | None): One of ``Sequence[str |
    Path]``, ``None``.
    settings_dir (str | Path | None): One of ``str``, ``Path``, ``None``.
  
  Returns:
    Path: Path produced by this call.
  
  Raises:
    FileNotFoundError: Raised when ``resolve_package_frontend_dir`` hits a
    ``FileNotFoundError`` failure path.
  
  Examples:
    >>> resolve_package_frontend_dir(None, None)  # doctest: +SKIP
  """
  if staticfiles_dirs:
    candidate = Path(staticfiles_dirs[0]) / "frontend"
    if candidate.is_dir():
      return candidate
  if settings_dir is not None:
    candidate = Path(settings_dir) / "static" / "frontend"
    if candidate.is_dir():
      return candidate
  raise FileNotFoundError(
    "package frontend directory not found under STATICFILES_DIRS or "
    "settings DIR/static/frontend"
  )


def _vite_volume_markers(frontend_root: Path) -> list[str]:
  """
  Internal helper to handle vite volume markers.
  
  Args:
    frontend_root (Path): String for frontend root.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _vite_volume_markers("x")  # doctest: +SKIP
  """
  markers: list[str] = []
  if (frontend_root / ".vite").exists():
    markers.append(str(frontend_root / ".vite"))
  root_index = frontend_root / "index.html"
  if root_index.is_file() and not (frontend_root / "machine" / "index.html").is_file():
    markers.append(str(root_index))
  return markers


def _atomic_replace_frontend(
  package_frontend: Path,
  dest_frontend: Path,
) -> None:
  """
  Internal helper to handle atomic replace frontend.
  
  Args:
    package_frontend (Path): String for package frontend.
    dest_frontend (Path): String for dest frontend.
  
  Returns:
    None
  
  Raises:
    Exception: Raised when ``_atomic_replace_frontend`` hits a ``Exception``
    failure path.
  
  Examples:
    >>> _atomic_replace_frontend("x", "x")  # doctest: +SKIP
  """
  parent = dest_frontend.parent
  parent.mkdir(parents=True, exist_ok=True)
  pid = os.getpid()
  staging = parent / f".frontend.heal-{pid}"
  backup = parent / f".frontend.bak-{pid}"
  if staging.exists():
    shutil.rmtree(staging)
  if backup.exists():
    shutil.rmtree(backup)
  try:
    shutil.copytree(package_frontend, staging)
    if dest_frontend.exists():
      dest_frontend.rename(backup)
    staging.rename(dest_frontend)
  except Exception:
    if not dest_frontend.exists() and backup.exists():
      backup.rename(dest_frontend)
    if staging.exists():
      shutil.rmtree(staging, ignore_errors=True)
    raise
  else:
    if backup.exists():
      shutil.rmtree(backup, ignore_errors=True)


def _fail_missing_shells(
  *,
  dest_frontend: Path,
  package: Path,
  missing: list[str],
  required: Sequence[str],
  err_stream: TextIO,
) -> None:
  """
  Internal helper to handle fail missing shells.
  
  Args:
    dest_frontend (Path): String for dest frontend.
    package (Path): String for package.
    missing (list[str]): Sequence for missing.
    required (Sequence[str]): Sequence for required.
    err_stream (TextIO): Err stream.
  
  Returns:
    None
  
  Raises:
    SystemExit: Raised when ``_fail_missing_shells`` hits a ``SystemExit``
    failure path.
  
  Examples:
    >>> _fail_missing_shells("x", "x", [], [], None)  # doctest: +SKIP
  """
  print(
    "ERROR: collectstatic did not produce required SPA shell(s):",
    file=err_stream,
  )
  for path in missing:
    print(f"  missing: {path}", file=err_stream)
  pkg_missing = (
    missing_required_shells(package, required) if package.is_dir() else list(required)
  )
  print(
    "  package frontend also missing required shell(s) "
    f"(dir={package}, missing={pkg_missing})",
    file=err_stream,
  )
  for marker in _vite_volume_markers(dest_frontend):
    print(f"  stale volume marker: {marker}", file=err_stream)
  print(
    "  Build image target hpcperfstats-full (or run scripts/rebuild_frontend.sh) "
    "so package static includes machine/ and pub/ SPA shells.",
    file=err_stream,
  )
  raise SystemExit(1)


def _heal_and_verify(
  *,
  package: Path,
  dest_frontend: Path,
  required: Sequence[str],
  reason: str,
  out_stream: TextIO,
  err_stream: TextIO,
) -> None:
  """
  Internal helper to handle heal and verify.
  
  Args:
    package (Path): String for package.
    dest_frontend (Path): String for dest frontend.
    required (Sequence[str]): Sequence for required.
    reason (str): String for reason.
    out_stream (TextIO): Out stream.
    err_stream (TextIO): Err stream.
  
  Returns:
    None
  
  Raises:
    SystemExit: Raised when ``_heal_and_verify`` hits a ``SystemExit`` failure
    path.
  
  Examples:
    >>> _heal_and_verify("x", "x", [], "x", None, None)  # doctest: +SKIP
  """
  print(
    f"SPA frontend auto-healed from package static ({reason}): "
    f"{package} -> {dest_frontend}",
    file=out_stream,
  )
  _atomic_replace_frontend(package, dest_frontend)
  still_missing = missing_required_shells(dest_frontend, required)
  if still_missing:
    print(
      "ERROR: SPA frontend auto-heal failed; shells still missing:",
      file=err_stream,
    )
    for path in still_missing:
      print(f"  missing: {path}", file=err_stream)
    raise SystemExit(1)
  print(
    "Verified SPA shells in STATIC_ROOT: " + ", ".join(required),
    file=out_stream,
  )


def ensure_spa_shells_in_static_root(
  *,
  static_root: str | Path,
  package_frontend: str | Path,
  required: Sequence[str] = REQUIRED_SPA_SHELLS,
  err: TextIO | None = None,
  out: TextIO | None = None,
) -> None:
  """
  Verify or auto-heal SPA shells under ``STATIC_ROOT/frontend``.
  
  Raises ``SystemExit(1)`` when shells remain missing after an attempted heal
  (or when the package frontend lacks required shells).
  
  Args:
    static_root (str | Path): One of ``str``, ``Path``.
    package_frontend (str | Path): One of ``str``, ``Path``.
    required (Sequence[str]): Sequence for required.
    err (TextIO | None): One of ``TextIO``, ``None``.
    out (TextIO | None): One of ``TextIO``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> ensure_spa_shells_in_static_root("x", "x", [], None, None)
  """
  err_stream = err if err is not None else sys.stderr
  out_stream = out if out is not None else sys.stdout
  dest_frontend = Path(static_root) / "frontend"
  package = Path(package_frontend)
  missing = missing_required_shells(dest_frontend, required)

  if not missing:
    pkg_fp = spa_shell_fingerprint(package)
    vol_fp = spa_shell_fingerprint(dest_frontend)
    if pkg_fp and pkg_fp == vol_fp:
      print(
        "Verified SPA shells in STATIC_ROOT: " + ", ".join(required),
        file=out_stream,
      )
      purge_nginx_config_from_public_frontend(dest_frontend, out=out_stream)
      return
    # Shells exist but package/volume machine/index.html content diverges
    # (typical after from-scratch image rebuild while staticfiles_data persists).
    if not package.is_dir() or not package_has_required_shells(package, required):
      _fail_missing_shells(
        dest_frontend=dest_frontend,
        package=package,
        missing=list(missing) or [str(dest_frontend / _MACHINE_SHELL)],
        required=required,
        err_stream=err_stream,
      )
    _heal_and_verify(
      package=package,
      dest_frontend=dest_frontend,
      required=required,
      reason=(
        f"content fingerprint drift machine/index.html "
        f"pkg={pkg_fp[:12] or 'MISSING'} vol={vol_fp[:12] or 'MISSING'}"
      ),
      out_stream=out_stream,
      err_stream=err_stream,
    )
    purge_nginx_config_from_public_frontend(dest_frontend, out=out_stream)
    return

  if not package.is_dir() or not package_has_required_shells(package, required):
    _fail_missing_shells(
      dest_frontend=dest_frontend,
      package=package,
      missing=missing,
      required=required,
      err_stream=err_stream,
    )

  _heal_and_verify(
    package=package,
    dest_frontend=dest_frontend,
    required=required,
    reason="stale volume",
    out_stream=out_stream,
    err_stream=err_stream,
  )
  purge_nginx_config_from_public_frontend(dest_frontend, out=out_stream)


def ensure_spa_shells_from_django_settings() -> None:
  """
  Entry point for ``django_startup.sh`` after ``collectstatic``.
  
  Returns:
    None
  
  Raises:
    SystemExit: Raised when ``ensure_spa_shells_from_django_settings`` hits a
    ``SystemExit`` failure path.
  
  Examples:
    >>> ensure_spa_shells_from_django_settings()  # doctest: +SKIP
  """
  from django.conf import settings
  from hpcperfstats.site.hpcperfstats_site import settings as site_settings_module

  try:
    package = resolve_package_frontend_dir(
      staticfiles_dirs=getattr(settings, "STATICFILES_DIRS", ()) or (),
      settings_dir=Path(site_settings_module.__file__).resolve().parent,
    )
  except FileNotFoundError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    print(
      "  Build image target hpcperfstats-full (or run scripts/rebuild_frontend.sh) "
      "so package static includes machine/ and pub/ SPA shells.",
      file=sys.stderr,
    )
    raise SystemExit(1) from exc
  ensure_spa_shells_in_static_root(
    static_root=settings.STATIC_ROOT,
    package_frontend=package,
  )
