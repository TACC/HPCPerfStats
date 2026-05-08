"""Anonymous read-only JSON endpoints for `/pub/` dashboards (pre-warmed artifacts only)."""

import hpcperfstats.conf_parser as cfg
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from hpcperfstats.site.machine.public_metrics_artifacts import (
    assemble_public_monthly_metrics_bundle,
)
from hpcperfstats.site.machine.renderers import SafeJSONRenderer
from hpcperfstats.site.machine.throttles import PublicClusterDashboardThrottle


class PublicClusterDashboardAggregateView(APIView):
  """Bundle every persisted expansion-factor histogram artifact for the public cluster dashboard."""

  permission_classes = [AllowAny]
  authentication_classes = []
  throttle_scope = "public_cluster_dashboard"
  throttle_classes = [PublicClusterDashboardThrottle]
  renderer_classes = [SafeJSONRenderer]
  parser_classes = []

  def get(self, request):
    bundle = assemble_public_monthly_metrics_bundle()
    payload = dict(bundle) if isinstance(bundle, dict) else bundle
    if isinstance(payload, dict):
      payload["machine_name"] = cfg.get_host_name_ext()
    response = Response(payload)
    if isinstance(payload, dict) and payload.get("status") == "ready":
      response["Cache-Control"] = (
          "public, max-age=120, stale-while-revalidate=300"
      )
    else:
      # Warm / transitional — avoid freezing "still loading" at browsers or CDNs.
      response["Cache-Control"] = (
          "private, max-age=0, must-revalidate, no-store"
      )
    return response
