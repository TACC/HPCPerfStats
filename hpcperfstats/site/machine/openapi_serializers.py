"""OpenAPI schema serializers for drf-spectacular (SPA-facing machine API)."""
from rest_framework import serializers


class ErrorDetailSerializer(serializers.Serializer):
    detail = serializers.CharField(required=False)
    error = serializers.CharField(required=False)
    login_url = serializers.CharField(required=False)


class SessionInfoSerializer(serializers.Serializer):
    logged_in = serializers.BooleanField()
    username = serializers.CharField()
    is_staff = serializers.BooleanField()
    machine_name = serializers.CharField()


class UserApiKeySerializer(serializers.Serializer):
    username = serializers.CharField()
    raw_key = serializers.CharField(allow_null=True, required=False)
    key_prefix = serializers.CharField()


class DropStaffResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    message = serializers.CharField()
    is_staff = serializers.BooleanField()


class InvalidateCacheRequestSerializer(serializers.Serializer):
    page_path = serializers.CharField()


class InvalidateCacheResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    page_path = serializers.CharField()
    deleted_keys = serializers.IntegerField()
    scanned_keys = serializers.IntegerField(required=False)
    matched_sample = serializers.ListField(child=serializers.CharField(), required=False)
    truncated_scan = serializers.BooleanField(required=False)


class HomeOptionsSerializer(serializers.Serializer):
    machine_name = serializers.CharField()
    year_list = serializers.ListField(child=serializers.IntegerField())
    date_list = serializers.JSONField()
    metrics = serializers.JSONField()
    queues = serializers.ListField(child=serializers.CharField())
    states = serializers.ListField(child=serializers.CharField())


class JobPerformanceSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.CharField(), required=False)
    tone = serializers.CharField(required=False)
    sort_rank = serializers.IntegerField(required=False)


class JobListEntrySerializer(serializers.Serializer):
    jid = serializers.CharField()
    submit_time = serializers.DateTimeField(allow_null=True, required=False)
    start_time = serializers.DateTimeField(allow_null=True, required=False)
    end_time = serializers.DateTimeField(allow_null=True, required=False)
    runtime = serializers.FloatField(allow_null=True, required=False)
    timelimit = serializers.FloatField(allow_null=True, required=False)
    node_hrs = serializers.FloatField(allow_null=True, required=False)
    nhosts = serializers.IntegerField(allow_null=True, required=False)
    ncores = serializers.IntegerField(allow_null=True, required=False)
    username = serializers.CharField(required=False)
    account = serializers.CharField(required=False)
    sample_count = serializers.IntegerField(required=False)
    queue = serializers.CharField(required=False)
    state = serializers.CharField(required=False)
    QOS = serializers.CharField(required=False)
    jobname = serializers.CharField(required=False)
    host_list = serializers.CharField(required=False)
    performance = JobPerformanceSerializer(required=False)
    color = serializers.CharField(required=False)


class JobListResponseSerializer(serializers.Serializer):
    nj = serializers.IntegerField(required=False)
    job_list = JobListEntrySerializer(many=True, required=False)
    filter_summary = serializers.JSONField(required=False)
    histograms = serializers.JSONField(required=False)


class JobDetailResponseSerializer(serializers.Serializer):
    job = serializers.JSONField()
    metrics = serializers.JSONField(required=False)
    gpu = serializers.JSONField(required=False)
    fsio = serializers.JSONField(required=False)


class JobPlotsResponseSerializer(serializers.Serializer):
    plots = serializers.JSONField()
    progressive = serializers.JSONField(required=False)


class TypeDetailResponseSerializer(serializers.Serializer):
    host_data = serializers.JSONField()
    metrics = serializers.JSONField(required=False)


class HostPlotResponseSerializer(serializers.Serializer):
    plot = serializers.JSONField()


class AdminMonitorResponseSerializer(serializers.Serializer):
    section = serializers.CharField(required=False)
    data = serializers.JSONField()


class JobMonitorResponseSerializer(serializers.Serializer):
    data = serializers.JSONField()


class JobMonitorGpuResponseSerializer(serializers.Serializer):
    data = serializers.JSONField()


class SacctIngestResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField(required=False)
    message = serializers.CharField(required=False)
    detail = serializers.CharField(required=False)


class PublicClusterDashboardSerializer(serializers.Serializer):
    status = serializers.CharField(required=False)
    machine_name = serializers.CharField(required=False)
    expansion_factors = serializers.JSONField(required=False)
    monthly_metrics = serializers.JSONField(required=False)
