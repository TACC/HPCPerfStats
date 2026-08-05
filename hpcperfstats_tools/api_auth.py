"""
Shared API auth helpers for CLI requests.
"""

from __future__ import annotations

from typing import Optional


def apply_api_key_header(
  headers: dict[str, str],
  api_key: Optional[str],
) -> dict[str, str]:
    """
    Apply header-only API-key authentication to request headers.
    
    Args:
      headers (dict[str, str]): Mapping for headers.
      api_key (Optional[str]): Api key, or None when absent.
    
    Returns:
      dict[str, str]: dict[str, str] produced by this call.
    
    Examples:
      >>> apply_api_key_header({}, None)  # doctest: +SKIP
    """
    if api_key:
        # Use explicit API-key header accepted by the server's newest auth flow.
        headers["X-API-Key"] = api_key
    return headers
