"""
Anonymous read-only JSON endpoints for `/pub/` dashboards (pre-warmed artifacts
  only).

Attributes:
  _LAZY_SECTION: Allowed ``section`` value for lazy expansion-factor requests.
  _LAZY_GROUPINGS: Allowed ``grouping`` values for lazy requests.
  _MONTHLY_PERIOD_RE: ``YYYY-MM`` validator for monthly periods.
  _YEARLY_PERIOD_RE: ``YYYY`` validator for yearly periods.
  _INVALID_QUERY_DETAIL: Generic non-reflective 400 detail string.
  _MISSING_PERIOD_DETAIL: Generic non-reflective 404 detail string.
"""
from __future__ import annotations

import re
from typing import Any

import hpcperfstats.dbload.lib.conf_parser as cfg
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from hpcperfstats.site.lib.machine.openapi_schema import PUBLIC_CLUSTER_DASHBOARD_SCHEMA
from hpcperfstats.site.lib.machine.public_metrics_artifacts import (
    assemble_public_dashboard_meta_bundle,
    assemble_public_monthly_metrics_bundle,
    load_public_expansion_factor_period,
)
from hpcperfstats.site.lib.machine.renderers import SafeJSONRenderer
from hpcperfstats.site.lib.machine.throttles import PublicClusterDashboardThrottle

_LAZY_SECTION = "expansion_factor"
_LAZY_GROUPINGS = frozenset({"monthly", "yearly"})
_MONTHLY_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_YEARLY_PERIOD_RE = re.compile(r"^\d{4}$")
_INVALID_QUERY_DETAIL = "Invalid or incomplete query parameters."
_MISSING_PERIOD_DETAIL = "Requested period is not available."


def _lazy_query_state(section: str, grouping: str, period: str) -> str:
  """
  Classify a public-dashboard lazy query tuple.

  Args:
    section (str): Normalized ``section`` query value (may be empty).
    grouping (str): Normalized ``grouping`` query value (may be empty).
    period (str): Raw ``period`` query value (may be empty).

  Returns:
    str: ``absent`` when no lazy params are present, ``ok`` when the tuple is
    syntactically valid, or ``invalid`` for incomplete/malformed combinations.

  Examples:
    >>> _lazy_query_state("", "", "")
    'absent'
    >>> _lazy_query_state("expansion_factor", "yearly", "2024")
    'ok'
    >>> _lazy_query_state("expansion_factor", "monthly", "")
    'invalid'
  """
  present = (bool(section), bool(grouping), bool(period))
  if not any(present):
    return "absent"
  if not all(present):
    return "invalid"
  if section != _LAZY_SECTION or grouping not in _LAZY_GROUPINGS:
    return "invalid"
  if grouping == "monthly" and not _MONTHLY_PERIOD_RE.fullmatch(period):
    return "invalid"
  if grouping == "yearly" and not _YEARLY_PERIOD_RE.fullmatch(period):
    return "invalid"
  return "ok"


class PublicClusterDashboardAggregateView(APIView):
  """
  Public cluster dashboard: meta by default; lazy period or full bundle via query.

  AllowAny anonymous JSON over SafeJSONRenderer. Invalid lazy query tuples return
  generic 400 bodies that never echo rejected input. Missing but well-formed
  periods return a generic 404.

  Attributes:
    permission_classes: DRF permission classes (AllowAny).
    authentication_classes: Empty list so sessions are not required.
    throttle_scope: Named throttle scope for this endpoint.
    throttle_classes: Throttle class list.
    renderer_classes: JSON renderer list.
    parser_classes: Empty parser list (GET-only).
  """

  permission_classes = [AllowAny]
  authentication_classes = []
  throttle_scope = "public_cluster_dashboard"
  throttle_classes = [PublicClusterDashboardThrottle]
  renderer_classes = [SafeJSONRenderer]
  parser_classes = []

  @PUBLIC_CLUSTER_DASHBOARD_SCHEMA
  def get(self, request: Any) -> Any:
    """
    Return public dashboard meta, a lazy expansion-factor block, or a full bundle.

    Args:
      request (Any): DRF request with optional ``section`` / ``grouping`` /
          ``period`` / ``full`` query parameters.

    Returns:
      Any: DRF ``Response`` with JSON payload and Cache-Control headers.

    Examples:
      >>> PublicClusterDashboardAggregateView().get(None)  # doctest: +SKIP
    """
    grouping = (request.GET.get("grouping") or "").strip().lower()
    period = (request.GET.get("period") or "").strip()
    section = (request.GET.get("section") or "").strip().lower()
    full_bundle = str(request.GET.get("full", "")).lower() in ("1", "true", "yes")

    lazy_state = _lazy_query_state(section, grouping, period)
    if lazy_state == "invalid":
      return Response(
          {
              "error": "invalid_request",
              "detail": _INVALID_QUERY_DETAIL,
          },
          status=status.HTTP_400_BAD_REQUEST,
      )

    if lazy_state == "ok":
      block = load_public_expansion_factor_period(grouping, period)
      if block is None:
        return Response(
            {
                "error": "period_not_available",
                "detail": _MISSING_PERIOD_DETAIL,
            },
            status=status.HTTP_404_NOT_FOUND,
        )
      payload = {
          "status": "ready",
          "section": "expansion_factor",
          "grouping": grouping,
          "period_key": period,
          "block": block,
          "machine_name": cfg.get_host_name_ext(),
      }
      response = Response(payload)
      response["Cache-Control"] = "public, max-age=120, stale-while-revalidate=300"
      return response

    if full_bundle:
      bundle = assemble_public_monthly_metrics_bundle()
    else:
      bundle = assemble_public_dashboard_meta_bundle()

    payload = dict(bundle) if isinstance(bundle, dict) else bundle
    if isinstance(payload, dict):
      payload["machine_name"] = cfg.get_host_name_ext()
    response = Response(payload)
    if isinstance(payload, dict) and payload.get("status") == "ready":
      response["Cache-Control"] = (
          "public, max-age=120, stale-while-revalidate=300"
      )
    else:
      response["Cache-Control"] = (
          "private, max-age=0, must-revalidate, no-store"
      )
    return response
