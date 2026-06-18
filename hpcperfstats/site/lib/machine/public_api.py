"""Anonymous read-only JSON endpoints for `/pub/` dashboards (pre-warmed artifacts only)."""

import hpcperfstats.dbload.lib.conf_parser as cfg
from drf_spectacular.utils import extend_schema
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


class PublicClusterDashboardAggregateView(APIView):
  """Public cluster dashboard: meta by default; lazy period or full bundle via query params."""

  permission_classes = [AllowAny]
  authentication_classes = []
  throttle_scope = "public_cluster_dashboard"
  throttle_classes = [PublicClusterDashboardThrottle]
  renderer_classes = [SafeJSONRenderer]
  parser_classes = []

  @PUBLIC_CLUSTER_DASHBOARD_SCHEMA
  def get(self, request):
    grouping = (request.GET.get("grouping") or "").strip().lower()
    period = (request.GET.get("period") or "").strip()
    section = (request.GET.get("section") or "").strip().lower()
    full_bundle = str(request.GET.get("full", "")).lower() in ("1", "true", "yes")

    if section == "expansion_factor" and grouping and period:
      block = load_public_expansion_factor_period(grouping, period)
      if block is None:
        return Response(
            {
                "error": "period_not_available",
                "detail": f"No histogram artifact for grouping={grouping} period={period}",
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
