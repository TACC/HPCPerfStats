"""
DRF throttles for authenticated and expensive machine API routes.
"""
from __future__ import annotations

from typing import Any

import hashlib

from django.core.exceptions import ImproperlyConfigured
from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


def _scope_throttle_rate(scope: str) -> str:
    """
    Internal helper to handle scope throttle rate.
    
    Args:
      scope (str): String for scope.
    
    Returns:
      str: str produced by this call.
    
    Raises:
      ImproperlyConfigured: Raised when ``_scope_throttle_rate`` hits a
      ``ImproperlyConfigured`` failure path.
    
    Examples:
      >>> _scope_throttle_rate("x")  # doctest: +SKIP
    """
    try:
        return api_settings.DEFAULT_THROTTLE_RATES[scope]
    except KeyError:
        raise ImproperlyConfigured(
            "No default throttle rate set for '%s' scope" % scope
        )


def _request_api_key_fingerprint(request: Any) -> Any:
    """
    Internal helper to handle request api key fingerprint.
    
    Args:
      request (Any): Request passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _request_api_key_fingerprint(None)  # doctest: +SKIP
    """
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if auth:
        parts = auth.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "api-key":
            return hashlib.sha256(parts[1].strip().encode("utf-8")).hexdigest()
    header_key = request.META.get("HTTP_X_API_KEY") or request.headers.get("X-API-Key")
    if header_key:
        return hashlib.sha256(header_key.strip().encode("utf-8")).hexdigest()
    return ""


class AuthenticatedUserOrApiKeyThrottle(SimpleRateThrottle):
    """
    Hold AuthenticatedUserOrApiKeyThrottle state and behavior.
    
    Subclasses ``SimpleRateThrottle``, extending that type with this class's
    fields and behavior.
    
    Subclasses ``SimpleRateThrottle``, extending that type with this class's
    fields and behavior.
    """
    scope = "authenticated_user_or_api_key"

    def get_rate(self) -> Any:
        """
        Return the rate.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> AuthenticatedUserOrApiKeyThrottle().get_rate()  # doctest: +SKIP
        """
        return _scope_throttle_rate(self.scope)

    def get_cache_key(self, request: Any, view: Any) -> Any:
        """
        Return the cache key.
        
        Args:
          request (Any): Request passed to this helper.
          view (Any): View passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> AuthenticatedUserOrApiKeyThrottle().get_cache_key(None, None)
        """
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


class ExpensiveReadThrottle(SimpleRateThrottle):
    """
    Hold ExpensiveReadThrottle state and behavior.
    
    Subclasses ``SimpleRateThrottle``, extending that type with this class's
    fields and behavior.
    
    Subclasses ``SimpleRateThrottle``, extending that type with this class's
    fields and behavior.
    """
    scope = "expensive_read"

    def get_rate(self) -> Any:
        """
        Return the rate.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> ExpensiveReadThrottle().get_rate()  # doctest: +SKIP
        """
        return _scope_throttle_rate(self.scope)

    def get_cache_key(self, request: Any, view: Any) -> Any:
        """
        Return the cache key.
        
        Args:
          request (Any): Request passed to this helper.
          view (Any): View passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> ExpensiveReadThrottle().get_cache_key(None, None)  # doctest: +SKIP
        """
        ident = self.get_ident(request)
        if not ident:
            return None
        return self.cache_format % {"scope": self.scope, "ident": ident}


class StaffIngestThrottle(SimpleRateThrottle):
    """
    Hold StaffIngestThrottle state and behavior.
    
    Subclasses ``SimpleRateThrottle``, extending that type with this class's
    fields and behavior.
    
    Subclasses ``SimpleRateThrottle``, extending that type with this class's
    fields and behavior.
    """
    scope = "staff_ingest"

    def get_rate(self) -> Any:
        """
        Return the rate.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> StaffIngestThrottle().get_rate()  # doctest: +SKIP
        """
        return _scope_throttle_rate(self.scope)

    def get_cache_key(self, request: Any, view: Any) -> Any:
        """
        Return the cache key.
        
        Args:
          request (Any): Request passed to this helper.
          view (Any): View passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> StaffIngestThrottle().get_cache_key(None, None)  # doctest: +SKIP
        """
        if not getattr(request, "session", None):
            return None
        username = (request.session.get("username") or "").strip()
        if not username:
            return None
        return self.cache_format % {"scope": self.scope, "ident": f"staff:{username}"}


class PublicClusterDashboardThrottle(ScopedRateThrottle):
    """
    Anonymous throttle for ``/api/pub/**`` cluster dashboard JSON (see.
    """

    scope = "public_cluster_dashboard"

    def get_cache_key(self, request: Any, view: Any) -> Any:
        """
        Return the cache key.
        
        Args:
          request (Any): Request passed to this helper.
          view (Any): View passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> PublicClusterDashboardThrottle().get_cache_key(None, None)
        """
        ident = self.get_ident(request)
        if not ident:
            return None
        return self.cache_format % {"scope": self.scope, "ident": ident}
