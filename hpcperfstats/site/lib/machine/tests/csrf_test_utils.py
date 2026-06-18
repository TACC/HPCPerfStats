"""Shared CSRF header helper for session-mutating API POST tests."""


def csrf_headers(token: str = "test-csrf-token") -> dict[str, str]:
  """Return Django test client / RequestFactory CSRF META headers."""
  return {"HTTP_X_CSRFTOKEN": token}
