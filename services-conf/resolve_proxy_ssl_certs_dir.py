#!/usr/bin/env python3
"""
Resolve and validate ``[DEFAULT] ssl_certs_dir`` for proxy image builds.

Prints the absolute host directory path to stdout for
``HPCPERFSTATS_SSL_CERTS_DIR`` / Compose ``additional_contexts``. Does **not**
copy PEM files into the git checkout.

Attributes:
  REQUIRED_PEM_NAMES: Basename tuple that must exist under ``ssl_certs_dir``.
  FIXTURE_REL: Checkout-relative path of the committed self-signed fixture.
"""

from __future__ import annotations

import argparse
import configparser
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


def validate_ssl_certs_dir(certs_dir: Path) -> Path:
  """
  Require *certs_dir* to be a directory containing required PEMs.

  Args:
    certs_dir (Path): Candidate host directory (may be relative).

  Returns:
    Path: Absolute resolved directory path.

  Raises:
    ValueError: When the path is missing, not a directory, or lacks
      ``fullchain.pem`` / ``privkey.pem``.

  Examples:
    >>> d = fixture_ssl_certs_dir()
    >>> validate_ssl_certs_dir(d) == d
    True
  """
  resolved = certs_dir.expanduser().resolve()
  if not resolved.is_dir():
    raise ValueError(f"ssl_certs_dir is not a directory: {resolved}")
  missing = [
      name for name in REQUIRED_PEM_NAMES if not (resolved / name).is_file()
  ]
  if missing:
    raise ValueError(
        f"ssl_certs_dir {resolved} missing required PEM(s): "
        + ", ".join(missing)
    )
  return resolved


def load_ssl_certs_dir_from_ini(ini_path: Path) -> Path:
  """
  Read ``[DEFAULT] ssl_certs_dir`` from *ini_path* and validate PEMs.

  Args:
    ini_path (Path): Path to ``hpcperfstats.ini`` or the example.

  Returns:
    Path: Absolute validated certificate directory.

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
  return validate_ssl_certs_dir(Path(raw))


def main(argv: list[str] | None = None) -> int:
  """
  Print a validated TLS certs directory path for Compose build wiring.

  Args:
    argv (list[str] | None): CLI arguments without the program name, or
      ``None`` to use ``sys.argv[1:]``.

  Returns:
    int: ``0`` on success; ``1`` when validation fails.

  Examples:
    >>> main(["--fixture"])
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
      help="print the committed tests/fixtures/proxy-ssl path (ignore --ini)",
  )
  args = parser.parse_args(argv)
  try:
    if args.fixture:
      path = validate_ssl_certs_dir(fixture_ssl_certs_dir())
    else:
      ini_path = (
          args.ini.resolve()
          if args.ini is not None
          else find_default_ini_path()
      )
      path = load_ssl_certs_dir_from_ini(ini_path)
  except ValueError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1
  print(path)
  return 0


if __name__ == "__main__":
  sys.exit(main())
