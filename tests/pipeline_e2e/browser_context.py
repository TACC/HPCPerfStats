"""Playwright contexts for the Compose proxy's test TLS certificate."""
from __future__ import annotations

from typing import Any


def new_browser_context(browser: Any, **kwargs: Any) -> Any:
  """Create a browser context that accepts the test proxy certificate."""
  return browser.new_context(ignore_https_errors=True, **kwargs)


def new_api_request_context(playwright: Any, **kwargs: Any) -> Any:
  """Create an API request context that accepts the test proxy certificate."""
  return playwright.request.new_context(ignore_https_errors=True, **kwargs)
