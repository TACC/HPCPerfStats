"""DRF serializers for machine app API. Job list/detail, HPCPerfStats Monitor, and form options."""
from rest_framework import serializers

from .job_list_performance import summarize_performance
from .models import job_data, metrics_data


class JobListSerializer(serializers.ModelSerializer):
    """Minimal job fields for list views."""

    performance = serializers.SerializerMethodField()
    color = serializers.SerializerMethodField()
    sample_count = serializers.SerializerMethodField()

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
            "sample_count",
            "queue",
            "state",
            "QOS",
            "jobname",
            "host_list",
            "performance",
            "color",
        ]

    def get_performance(self, obj):
        """Structured performance column: labels, tone, sort_rank (see job_list_performance)."""
        has_row = getattr(obj, "has_metrics_data", None)
        if has_row is None:
            has_row = obj.metrics_data_set.exists()
        mcount = getattr(obj, "metrics_value_count", None)
        if mcount is None:
            mcount = obj.metrics_data_set.filter(value__isnull=False).count()
        return summarize_performance(
            has_metrics_row=bool(has_row),
            metrics_value_count=int(mcount),
            distinct_time_count=obj.metrics_distinct_time_count,
            runtime=obj.runtime,
        )

    def get_color(self, obj):
        """Return hex color for the job's state (completed/failed/other)."""
        return obj.color()

    def get_sample_count(self, obj):
        """Expose metrics sample count used by staff job-list troubleshooting."""
        return obj.metrics_distinct_time_count


class MetricsDataSerializer(serializers.ModelSerializer):
    """Metrics data fields (type, metric, units, value) for embedding in job detail."""

    class Meta:
        model = metrics_data
        fields = ["type", "metric", "units", "value"]
