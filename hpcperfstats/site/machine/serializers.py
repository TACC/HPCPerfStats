"""DRF serializers for machine app API. Job list/detail, admin monitor, and form options."""
from rest_framework import serializers

from .models import job_data, metrics_data


class JobListSerializer(serializers.ModelSerializer):
    """Minimal job fields for list views."""

    has_metrics = serializers.SerializerMethodField()
    color = serializers.SerializerMethodField()

    class Meta:
        model = job_data
        fields = [
            "jid",
            "submit_time",
            "start_time",
            "end_time",
            "runtime",
            "timelimit",
            "node_hrs",
            "nhosts",
            "ncores",
            "username",
            "account",
            "queue",
            "state",
            "QOS",
            "jobname",
            "host_list",
            "has_metrics",
            "color",
        ]

    def get_has_metrics(self, obj):
        return obj.metrics_data_set.exists()

    def get_color(self, obj):
        return obj.color()


class MetricsDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = metrics_data
        fields = ["type", "metric", "units", "value"]
