"""
DRF serializers for machine app API. Job list/detail, HPCPerfStats Monitor, and
  form options.
"""
from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .job_list_performance import summarize_performance
from .models import job_data, metrics_data


class JobListSerializer(serializers.ModelSerializer):
    """
    Minimal job fields for list views.
    """

    performance = serializers.SerializerMethodField()
    color = serializers.SerializerMethodField()
    sample_count = serializers.SerializerMethodField()

    class Meta:
        """
        Django model metadata for the enclosing model.
        """
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

    def get_performance(self, obj: Any) -> Any:
        """
        Structured performance column: labels, tone, sort_rank (see.
        
          job_list_performance).
        
        Args:
          obj (Any): Value to inspect (typically a numeric scalar).
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> JobListSerializer().get_performance(None)  # doctest: +SKIP
        """
        has_row = getattr(obj, "has_metrics_data", None)
        if has_row is None:
            has_row = obj.metrics_data_set.exists()
        mcount = getattr(obj, "metrics_value_count", None)
        if mcount is None:
            mcount = obj.metrics_data_set.filter(value__isnull=False).count()
        plots_ready = getattr(obj, "plots_artifacts_ready", None)
        if plots_ready is None:
            plots_ready = False
        return summarize_performance(
            has_metrics_row=bool(has_row),
            metrics_value_count=int(mcount),
            distinct_time_count=obj.metrics_distinct_time_count,
            runtime=obj.runtime,
            plots_artifacts_ready=bool(plots_ready),
        )

    def get_color(self, obj: Any) -> Any:
        """
        Return hex color for the job's state (completed/failed/other).
        
        Args:
          obj (Any): Value to inspect (typically a numeric scalar).
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> JobListSerializer().get_color(None)  # doctest: +SKIP
        """
        return obj.color()

    def get_sample_count(self, obj: Any) -> Any:
        """
        Expose metrics sample count used by staff job-list troubleshooting.
        
        Args:
          obj (Any): Value to inspect (typically a numeric scalar).
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> JobListSerializer().get_sample_count(None)  # doctest: +SKIP
        """
        return obj.metrics_distinct_time_count


class MetricsDataSerializer(serializers.ModelSerializer):
    """
    Metrics data fields (type, metric, units, value) for embedding in job.
    """

    class Meta:
        """
        Django model metadata for the enclosing model.
        """
        model = metrics_data
        fields = ["type", "metric", "units", "value"]
