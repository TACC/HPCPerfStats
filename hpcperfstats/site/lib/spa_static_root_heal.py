"""Ensure Next SPA shells exist under STATIC_ROOT/frontend after collectstatic.

Persistent ``staticfiles_data`` volumes can retain a Vite-era ``frontend/`` tree
where ``collectstatic`` reports unmodified files and never materializes
``machine/index.html`` / ``pub/index.html``. When the image package static still
has those shells, replace ``STATIC_ROOT/frontend`` from package static.

Volumes can also retain an older Next export whose shells exist but whose
``machine/index.html`` (and hashed chunks) differ from the image package after a
from-scratch Docker rebuild. Compare sha256 fingerprints and replace on drift.
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


def missing_required_shells(
  frontend_root: str | Path,
  required: Sequence[str] = REQUIRED_SPA_SHELLS,
) -> list[str]:
  root = Path(frontend_root)
  return [str(root / rel) for rel in required if not (root / rel).is_file()]


def package_has_required_shells(
  package_frontend: str | Path,
  required: Sequence[str] = REQUIRED_SPA_SHELLS,
) -> bool:
  return not missing_required_shells(package_frontend, required)


def spa_shell_fingerprint(frontend_root: str | Path) -> str:
  """Return sha256 hex of ``machine/index.html``, or ``\"\"`` if missing."""
  path = Path(frontend_root) / _MACHINE_SHELL
  if not path.is_file():
    return ""
  return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_package_frontend_dir(
  *,
  staticfiles_dirs: Sequence[str | Path] | None = None,
  settings_dir: str | Path | None = None,
) -> Path:
  """Locate package ``…/static/frontend`` from Django settings paths."""
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
  markers: list[str] = []
  if (frontend_root / ".vite").exists():
    markers.append(str(frontend_root / ".vite"))
  root_index = frontend_root / "index.html"
  if root_index.is_file() and not (frontend_root / "machine" / "index.html").is_file():
    markers.append(str(root_index))
  return markers


def _atomic_replace_frontend(package_frontend: Path, dest_frontend: Path) -> None:
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
  """Verify or auto-heal SPA shells under ``STATIC_ROOT/frontend``.

  Raises ``SystemExit(1)`` when shells remain missing after an attempted heal
  (or when the package frontend lacks required shells).
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


def ensure_spa_shells_from_django_settings() -> None:
  """Entry point for ``django_startup.sh`` after ``collectstatic``."""
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
