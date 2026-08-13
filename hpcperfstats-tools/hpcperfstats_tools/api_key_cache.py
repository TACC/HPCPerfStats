"""
Shared API key file cache for HPCPerfStats CLIs.

Attributes:
  API_KEY_CACHE: Path used for load/save of cached API keys (I/O only).
  API_KEY_CACHE_DISPLAY: User-facing path hint for help text (plain str;
    not derived from API_KEY_CACHE so static analysis does not treat help
    prints as clear-text credential logging).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

API_KEY_CACHE = Path.home() / ".hpcperfstats-api"
API_KEY_CACHE_DISPLAY = "~/.hpcperfstats-api"


def load_cached_api_key(api_url: str) -> Optional[str]:
  """
  Load API key for api_url from ~/.hpcperfstats-api if present.
  
  Supported formats:
  - Single line file with just the key (applies to all URLs)
  - One mapping per line: '<base_url> <key>'
  Lines starting with '#' are ignored.
  
  Args:
    api_url (str): String for api url.
  
  Returns:
    Optional[str]: Optional[str] — the result, or None when unavailable.
  
  Examples:
    >>> load_cached_api_key("x")  # doctest: +SKIP
  """
  if not API_KEY_CACHE.exists():
    return None
  try:
    text = API_KEY_CACHE.read_text(encoding="utf-8")
  except OSError:
    return None
  lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
  if not lines:
    return None
  # Single-key mode
  if len(lines) == 1 and " " not in lines[0]:
    return lines[0]
  base = api_url.rstrip("/")
  for line in lines:
    if line.startswith("#"):
      continue
    parts = line.split(None, 1)
    if len(parts) != 2:
      continue
    url, key = parts
    if url.rstrip("/") == base:
      return key
  return None


def save_cached_api_key(api_url: str, api_key: str) -> None:
  """
  Persist API key for api_url into ~/.hpcperfstats-api.
  
  Args:
    api_url (str): String for api url.
    api_key (str): String for api key.
  
  Returns:
    None
  
  Examples:
    >>> save_cached_api_key("x", "x")  # doctest: +SKIP
  """
  base = api_url.rstrip("/")
  lines = []
  if API_KEY_CACHE.exists():
    try:
      existing = API_KEY_CACHE.read_text(encoding="utf-8")
      lines = existing.splitlines()
    except OSError:
      lines = []
  # Remove any previous mapping for this URL and any legacy single-key lines
  new_lines = []
  for line in lines:
    if not line.strip():
      continue
    if line.lstrip().startswith("#"):
      new_lines.append(line)
      continue
    parts = line.split(None, 1)
    # Drop legacy single-key lines (no URL) or old mapping for this base URL
    if len(parts) == 1:
      continue
    if len(parts) == 2 and parts[0].rstrip("/") == base:
      continue
    new_lines.append(line)
  new_lines.append(f"{base} {api_key}")
  try:
    API_KEY_CACHE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
  except OSError:
    # Failing to cache should not break the CLI.
    pass


def api_key_help_url(api_url: str) -> str:
  """
  Best-effort URL of the browsable page where the user can create an API key.
  
  Prefer env override HPCPERF_API_KEY_URL; otherwise strip /api/ and point to
    /api-key/.
  
  Args:
    api_url (str): String for api url.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> api_key_help_url("x")  # doctest: +SKIP
  """
  override = os.environ.get("HPCPERF_API_KEY_URL")
  if override:
    return override
  root = api_url
  if "/api/" in root:
    root = root.split("/api/", 1)[0]
  root = root.rstrip("/")
  return root + "/api-key/"
