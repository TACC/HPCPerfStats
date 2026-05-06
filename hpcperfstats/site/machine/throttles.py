"""DRF throttles for authenticated and expensive machine API routes."""

import hashlib

from rest_framework.throttling import ScopedRateThrottle


def _request_api_key_fingerprint(request):
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if auth:
        parts = auth.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "api-key":
            return hashlib.sha256(parts[1].strip().encode("utf-8")).hexdigest()
    header_key = request.META.get("HTTP_X_API_KEY") or request.headers.get("X-API-Key")
    if header_key:
        return hashlib.sha256(header_key.strip().encode("utf-8")).hexdigest()
    return ""


class AuthenticatedUserOrApiKeyThrottle(ScopedRateThrottle):
    scope = "authenticated_user_or_api_key"

    def get_cache_key(self, request, view):
        if not getattr(request, "session", None):
            return None
        username = (request.session.get("username") or "").strip()
        if username:
            ident = f"user:{username}"
        else:
            api_key_hash = _request_api_key_fingerprint(request)
            if not api_key_hash:
                return None
            ident = f"api-key:{api_key_hash}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ExpensiveReadThrottle(ScopedRateThrottle):
    scope = "expensive_read"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        if not ident:
            return None
        return self.cache_format % {"scope": self.scope, "ident": ident}


class StaffIngestThrottle(ScopedRateThrottle):
    scope = "staff_ingest"

    def get_cache_key(self, request, view):
        if not getattr(request, "session", None):
            return None
        username = (request.session.get("username") or "").strip()
        if not username:
            return None
        return self.cache_format % {"scope": self.scope, "ident": f"staff:{username}"}
