#!/usr/bin/env python3
"""
Resolve and validate ``[DEFAULT] ssl_certs_dir`` for proxy image builds.

Reads the path from ``hpcperfstats.ini`` (not from operator ``.env`` / shell
exports). The **proxy Dockerfile** invokes this helper during ``docker compose
build`` / ``podman build`` with ``--host-prefix /host --dest-dir
/etc/ssl/hpcperfstats`` so operators do not run a separate materialize step.
Optionally can still write ``.hpcperfstats_ssl_certs/`` on the host.

Attributes:
  REQUIRED_PEM_NAMES: Basename tuple that must exist under ``ssl_certs_dir``.
  FIXTURE_REL: Checkout-relative path of the committed self-signed fixture.
  COMPOSE_SSL_CERTS_CONTEXT_REL: Optional gitignored host-side bake directory.
"""

from __future__ import annotations

import argparse
import configparser
import os
import shutil
import stat
import sys
from pathlib import Path

REQUIRED_PEM_NAMES: tuple[str, ...] = ("fullchain.pem", "privkey.pem")
FIXTURE_REL: str = "tests/fixtures/proxy-ssl"
COMPOSE_SSL_CERTS_CONTEXT_REL: str = ".hpcperfstats_ssl_certs"


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


def find_default_ini_path(*, cwd: Path | None = None) -> Path:
  """
  Locate ``hpcperfstats.ini`` or ``hpcperfstats.ini.example`` under *cwd*.

  Prefers the deployment ini when both exist.

  Args:
    cwd (Path | None): Directory to search; defaults to the process cwd.

  Returns:
    Path: Absolute path of the first matching ini file.

  Raises:
    ValueError: When neither file exists in *cwd*.

  Examples:
    >>> p = find_default_ini_path(cwd=repo_root())
    >>> p.name in ("hpcperfstats.ini", "hpcperfstats.ini.example")
    True
  """
  base = (cwd if cwd is not None else Path.cwd()).resolve()
  for name in ("hpcperfstats.ini", "hpcperfstats.ini.example"):
    candidate = base / name
    if candidate.is_file():
      return candidate
  raise ValueError(
      "missing hpcperfstats.ini or hpcperfstats.ini.example under "
      f"{base} (pass --ini)"
  )


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


def resolve_readable_pem(certs_dir: Path, name: str) -> Path:
  """
  Resolve *name* under *certs_dir* to a readable regular file.

  Follows Let’s Encrypt ``live/`` symlinks into ``archive/``.

  Args:
    certs_dir (Path): Absolute certificate directory.
    name (str): Basename such as ``fullchain.pem``.

  Returns:
    Path: Absolute path of the readable PEM file (symlink target when linked).

  Raises:
    ValueError: When the path is missing, a broken symlink, not a file, or
      not readable.

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
  if not real.is_file():
    raise ValueError(f"{name} resolves to a non-file: {link} -> {real}")
  if not os.access(real, os.R_OK):
    raise ValueError(f"{name} is not readable: {real}")
  return real


def validate_ssl_certs_dir(certs_dir: Path) -> Path:
  """
  Require *certs_dir* to be a directory containing readable required PEMs.

  Args:
    certs_dir (Path): Candidate host directory (may be relative).

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
    raise ValueError(f"ssl_certs_dir is not a directory: {resolved}")
  errors: list[str] = []
  for name in REQUIRED_PEM_NAMES:
    try:
      resolve_readable_pem(resolved, name)
    except ValueError as exc:
      errors.append(str(exc))
  if errors:
    listing = sorted(resolved.iterdir()) if os.access(resolved, os.R_OK) else []
    names = ", ".join(p.name for p in listing) or "(unreadable or empty)"
    raise ValueError(
        f"ssl_certs_dir {resolved} PEM check failed: "
        + "; ".join(errors)
        + f"; directory entries: {names}"
    )
  return resolved


def under_host_prefix(path: Path, host_prefix: Path | None) -> Path:
  """
  Map an absolute host path into a BuildKit ``/host`` bind mount.

  Args:
    path (Path): Absolute path as written in ``hpcperfstats.ini``.
    host_prefix (Path | None): Prefix such as ``/host`` when baking inside
      a container build; ``None`` leaves *path* unchanged.

  Returns:
    Path: Path visible inside the build environment.

  Examples:
    >>> under_host_prefix(Path('/etc/x'), Path('/host')) == Path('/host/etc/x')
    True
  """
  if host_prefix is None:
    return path
  abs_path = path if path.is_absolute() else path.resolve()
  return host_prefix.joinpath(*abs_path.parts[1:])


def load_ssl_certs_dir_from_ini(
    ini_path: Path,
    *,
    host_prefix: Path | None = None,
) -> Path:
  """
  Read ``[DEFAULT] ssl_certs_dir`` from *ini_path* and validate PEMs.

  Args:
    ini_path (Path): Path to ``hpcperfstats.ini`` or the example.
    host_prefix (Path | None): Optional BuildKit host bind prefix (``/host``).

  Returns:
    Path: Absolute validated certificate directory (under *host_prefix* when set).

  Raises:
    ValueError: When the ini cannot be read, the key is empty, or PEMs are
      missing.

  Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> fix = fixture_ssl_certs_dir()
    >>> td = Path(tempfile.mkdtemp())
    >>> ini = td / "t.ini"
    >>> _ = ini.write_text(
    ...     "[DEFAULT]\\nssl_certs_dir = %s\\n" % fix, encoding="utf-8"
    ... )
    >>> load_ssl_certs_dir_from_ini(ini) == fix
    True
  """
  cfg = configparser.ConfigParser(
      interpolation=configparser.ExtendedInterpolation()
  )
  read_ok = cfg.read(ini_path, encoding="utf-8")
  if not read_ok:
    raise ValueError(f"could not read ini: {ini_path}")
  raw = cfg.get("DEFAULT", "ssl_certs_dir", fallback="").strip()
  if not raw:
    raise ValueError(
        f"missing or empty [DEFAULT] ssl_certs_dir= in {ini_path}"
    )
  return validate_ssl_certs_dir(under_host_prefix(Path(raw), host_prefix))


def copy_pems_preserving_meta(certs_dir: Path, dest_dir: Path) -> Path:
  """
  Copy required PEMs into *dest_dir*, preserving source file and dir metadata.

  Args:
    certs_dir (Path): Validated source directory (may be under ``/host``).
    dest_dir (Path): Destination directory (created if needed).

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
  validated = validate_ssl_certs_dir(certs_dir)
  dest_dir.mkdir(parents=True, exist_ok=True)
  apply_stat_ownership_and_mode(dest_dir, validated.stat())
  for name in REQUIRED_PEM_NAMES:
    src = resolve_readable_pem(validated, name)
    src_st = src.stat()
    out = dest_dir / name
    if out.exists() or out.is_symlink():
      out.unlink()
    shutil.copy2(src, out)
    apply_stat_ownership_and_mode(out, src_st)
  return dest_dir


def compose_ssl_certs_context_path(
    *,
    checkout_root: Path | None = None,
) -> Path:
  """
  Return the absolute path of the optional host-side TLS bake directory.

  Args:
    checkout_root (Path | None): Checkout root; defaults to this script's repo.

  Returns:
    Path: Absolute path of ``.hpcperfstats_ssl_certs``.

  Examples:
    >>> compose_ssl_certs_context_path().name == COMPOSE_SSL_CERTS_CONTEXT_REL
    True
  """
  root = (checkout_root if checkout_root is not None else repo_root()).resolve()
  return root / COMPOSE_SSL_CERTS_CONTEXT_REL


def ensure_compose_ssl_certs_context(
    certs_dir: Path,
    *,
    checkout_root: Path | None = None,
) -> Path:
  """
  Copy required PEMs into ``.hpcperfstats_ssl_certs/`` with exact source perms.

  Optional host-side helper; production proxy builds bake via the Dockerfile
  (``--host-prefix`` / ``--dest-dir``) instead. Never ``rm -rf`` through a
  symlink into ``/etc/letsencrypt``.

  Args:
    certs_dir (Path): Absolute validated host directory with required PEMs.
    checkout_root (Path | None): Checkout root that owns the context dir;
      defaults to this script's repo root.

  Returns:
    Path: Absolute path of ``.hpcperfstats_ssl_certs``.

  Raises:
    ValueError: When a required PEM cannot be resolved, or an unexpected path
      blocks the context directory name.
    OSError: When the directory or copies cannot be created.

  Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> fix = fixture_ssl_certs_dir()
    >>> td = Path(tempfile.mkdtemp())
    >>> dest = ensure_compose_ssl_certs_context(fix, checkout_root=td)
    >>> (dest / "fullchain.pem").is_file() and not (dest / "fullchain.pem").is_symlink()
    True
  """
  validated = validate_ssl_certs_dir(certs_dir)
  root = (checkout_root if checkout_root is not None else repo_root()).resolve()
  dest = root / COMPOSE_SSL_CERTS_CONTEXT_REL
  if dest.is_symlink() or dest.is_file():
    # Unlink only — never follow a symlink into Let's Encrypt live/.
    dest.unlink()
  elif dest.is_dir():
    shutil.rmtree(dest)
  elif dest.exists():
    raise ValueError(
        f"refuse to replace unexpected compose SSL context path: {dest}"
    )
  return copy_pems_preserving_meta(validated, dest)


def _default_compose_link_repo_root(
    *,
    repo_root_arg: Path | None,
) -> Path:
  """
  Choose the checkout root that should own ``.hpcperfstats_ssl_certs``.

  Args:
    repo_root_arg (Path | None): Explicit ``--repo-root`` value, if any.

  Returns:
    Path: Absolute directory for the Compose SSL context directory.

  Examples:
    >>> _default_compose_link_repo_root(repo_root_arg=None).is_dir()
    True
  """
  if repo_root_arg is not None:
    return repo_root_arg.resolve()
  cwd = Path.cwd().resolve()
  if (cwd / "docker-compose.yaml").is_file():
    return cwd
  return repo_root()


def main(argv: list[str] | None = None) -> int:
  """
  Print a validated TLS certs directory; optionally bake PEMs to a destination.

  Args:
    argv (list[str] | None): CLI arguments without the program name, or
      ``None`` to use ``sys.argv[1:]``.

  Returns:
    int: ``0`` on success; ``1`` when validation fails.

  Examples:
    >>> main(["--fixture", "--no-link"])
    0
  """
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--ini",
      type=Path,
      default=None,
      help="hpcperfstats.ini path (default: cwd ini or .example)",
  )
  parser.add_argument(
      "--fixture",
      action="store_true",
      help="use committed tests/fixtures/proxy-ssl (ignore --ini); CI only",
  )
  parser.add_argument(
      "--no-link",
      action="store_true",
      help="print path only; do not update .hpcperfstats_ssl_certs",
  )
  parser.add_argument(
      "--repo-root",
      type=Path,
      default=None,
      help="checkout root for optional .hpcperfstats_ssl_certs (default: cwd)",
  )
  parser.add_argument(
      "--host-prefix",
      type=Path,
      default=None,
      help="BuildKit bind prefix for host paths (e.g. /host in proxy.Dockerfile)",
  )
  parser.add_argument(
      "--dest-dir",
      type=Path,
      default=None,
      help="copy PEMs here (proxy image bake); skips .hpcperfstats_ssl_certs",
  )
  args = parser.parse_args(argv)
  try:
    host_prefix = args.host_prefix
    if args.fixture:
      path = validate_ssl_certs_dir(
          under_host_prefix(fixture_ssl_certs_dir(), host_prefix)
      )
    else:
      ini_path = (
          args.ini.resolve()
          if args.ini is not None
          else find_default_ini_path()
      )
      path = load_ssl_certs_dir_from_ini(ini_path, host_prefix=host_prefix)
    if args.dest_dir is not None:
      copy_pems_preserving_meta(path, args.dest_dir)
      print(
          f"baked PEMs into {args.dest_dir} from {path}",
          file=sys.stderr,
      )
    elif not args.no_link:
      link_root = _default_compose_link_repo_root(repo_root_arg=args.repo_root)
      dest = ensure_compose_ssl_certs_context(path, checkout_root=link_root)
      print(
          f"compose SSL context: {dest} (PEM copies from {path}, "
          f"mode/uid/gid preserved)",
          file=sys.stderr,
      )
  except ValueError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1
  print(path)
  return 0


if __name__ == "__main__":
  sys.exit(main())
