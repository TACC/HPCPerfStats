"""Anonymous read-only JSON endpoints for `/pub/` dashboards (pre-warmed artifacts only)."""

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from hpcperfstats.site.machine.public_metrics_artifacts import (
    assemble_public_monthly_metrics_bundle,
)
from hpcperfstats.site.machine.renderers import SafeJSONRenderer
from hpcperfstats.site.machine.throttles import PublicMonthlyMetricsThrottle


class PublicMonthlyMetricsAggregateView(APIView):
  """Bundle every persisted expansion-factor histogram artifact for Monthly Metrics."""

  permission_classes = [AllowAny]
  authentication_classes = []
  throttle_scope = "public_monthly_metrics"
  throttle_classes = [PublicMonthlyMetricsThrottle]
  renderer_classes = [SafeJSONRenderer]
  parser_classes = []

  def get(self, request):
    bundle = assemble_public_monthly_metrics_bundle()
    response = Response(bundle)
    response["Cache-Control"] = "public, max-age=120, stale-while-revalidate=300"
    return response
