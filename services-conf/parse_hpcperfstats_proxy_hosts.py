"""Parse [DEFAULT] server= hostnames from hpcperfstats.ini for the TLS proxy."""

from __future__ import annotations

import configparser
import re
from pathlib import Path

_SERVER_NAME_PART_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]*$")


def load_allowed_server_names(ini_path: Path) -> list[str]:
  """Return trimmed hostnames from ``[DEFAULT] server=`` (comma-separated)."""
  cfg = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
  read_ok = cfg.read(ini_path, encoding="utf-8")
  if not read_ok:
    raise ValueError(f"could not read ini: {ini_path}")
  raw = cfg.get("DEFAULT", "server", fallback="").strip()
  if not raw:
    raise ValueError(f"missing or empty [DEFAULT] server= in {ini_path}")
  names = [part.strip() for part in raw.split(",")]
  names = [n for n in names if n]
  if not names:
    raise ValueError(f"[DEFAULT] server= had no hostnames after parsing in {ini_path}")
  for name in names:
    if not _SERVER_NAME_PART_RE.fullmatch(name):
      raise ValueError(
          f"invalid hostname for nginx server_name (allowed [a-zA-Z0-9.-]): {name!r}"
      )
  return names
