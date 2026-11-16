"""
Configuration helpers for the hpcperfstats-tools package.

The tools read the API base URL from the INI file named by the
HPCPERFSTATS_TOOLS_INI environment variable (no default path), e.g.::

    [API]
    base_url = http://localhost:8000/api/

Attributes:
  _CONFIG_PATH: INI path that was loaded at import, or None.
  _cfg: Parser holding the loaded INI contents.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

_cfg = configparser.ConfigParser()


def _load_config() -> Path | None:
  """
  Load configuration from HPCPERFSTATS_TOOLS_INI if set.

  Returns:
    Optional[Path]: Optional[Path] - the result, or None when unavailable.

  Examples:
    >>> _load_config()  # doctest: +SKIP
  """
  env_path = os.environ.get("HPCPERFSTATS_TOOLS_INI")
  if not env_path:
    return None
  path = Path(env_path).expanduser()
  try:
    if path.is_file():
      _cfg.read(path)
      return path
  except OSError:
    pass
  return None


_CONFIG_PATH = _load_config()


def get_api_base_url(
  default: str | None = "http://localhost:8000/api/",
) -> str | None:
  """
  Return the base URL for the HPCPerfStats REST API.

  Loaded only from the tools INI file ([API] base_url). Config file path must
  be set via HPCPERFSTATS_TOOLS_INI. Falls back to default if not set.

  Args:
    default (Optional[str]): Default, or None when absent.

  Returns:
    Optional[str]: Optional[str] - the result, or None when unavailable.

  Examples:
    >>> get_api_base_url(None)  # doctest: +SKIP
  """
  if _cfg.has_section("API") and _cfg.has_option("API", "base_url"):
    value = _cfg.get("API", "base_url").strip()
    if value:
      return value

  return default
