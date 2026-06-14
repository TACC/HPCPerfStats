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


class HomeMetricOptionSerializer(serializers.Serializer):
    type = serializers.CharField()
    metric = serializers.CharField()
    units = serializers.CharField()


class HomeDateDayPairSerializer(serializers.Serializer):
    """One [iso_date, day_of_month] pair from home date_list."""

    date = serializers.CharField()
    day = serializers.CharField()


class HomeDateMonthEntrySerializer(serializers.Serializer):
    """One month bucket: month key plus day pairs (serialized from API tuple)."""

    month = serializers.CharField()
    days = HomeDateDayPairSerializer(many=True)


class HomeOptionsSerializer(serializers.Serializer):
    machine_name = serializers.CharField()
    year_list = serializers.ListField(child=serializers.IntegerField())
    date_list = serializers.ListField(
        child=serializers.ListField(
            child=serializers.JSONField(),
            min_length=2,
            max_length=2,
        )
    )
    metrics = HomeMetricOptionSerializer(many=True)
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


class JobListFilterSummarySerializer(serializers.Serializer):
    nj = serializers.IntegerField(required=False)
    filter_text = serializers.CharField(required=False, allow_blank=True)


class JobListHistogramEnvelopeSerializer(serializers.Serializer):
    """Histogram metadata without embedded Bokeh plot_item payloads."""

    title = serializers.CharField(required=False, allow_blank=True)
    metric = serializers.CharField(required=False, allow_blank=True)
    queue = serializers.CharField(required=False, allow_blank=True)
    unavailable_reason = serializers.CharField(required=False, allow_null=True)


class JobListResponseSerializer(serializers.Serializer):
    nj = serializers.IntegerField(required=False)
    job_list = JobListEntrySerializer(many=True, required=False)
    filter_summary = JobListFilterSummarySerializer(required=False)
    histograms = serializers.DictField(child=JobListHistogramEnvelopeSerializer(), required=False)


class JobDetailJobSerializer(JobListEntrySerializer):
    """Job detail primary job_data row (extends list entry fields)."""

    derived_data_status = serializers.CharField(required=False)
    client_url = serializers.CharField(required=False, allow_null=True)
    server_url = serializers.CharField(required=False, allow_null=True)


class JobDetailResponseSerializer(serializers.Serializer):
    job_data = JobDetailJobSerializer(required=False)
    host_list = serializers.ListField(child=serializers.CharField(), required=False)
    metrics_list = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField(allow_blank=True)),
        required=False,
    )
    metrics = serializers.JSONField(required=False)
    gpu = serializers.JSONField(required=False)
    fsio = serializers.JSONField(required=False)
    xalt_data = serializers.DictField(required=False)
    schema = serializers.DictField(required=False)
    proc_list = serializers.ListField(child=serializers.DictField(), required=False)
    derived_data_status = serializers.CharField(required=False)
    client_url = serializers.CharField(required=False, allow_null=True)
    server_url = serializers.CharField(required=False, allow_null=True)
    multiprecision_cpu_plot_item = serializers.JSONField(required=False)
    multiprecision_gpu_plot_item = serializers.JSONField(required=False)


class JobPlotsResponseSerializer(serializers.Serializer):
    plots = serializers.JSONField()
    progressive = serializers.JSONField(required=False)


class TypeDetailResponseSerializer(serializers.Serializer):
    host_data = serializers.JSONField()
    metrics = serializers.JSONField(required=False)


class HostPlotResponseSerializer(serializers.Serializer):
    host = serializers.CharField()
    plot_item = serializers.JSONField(allow_null=True)
    plot_unavailable_reason = serializers.CharField(allow_null=True, required=False)
    end_time__gte = serializers.CharField()
    end_time__lte = serializers.CharField()


class AdminMonitorResponseSerializer(serializers.Serializer):
    section = serializers.CharField(required=False)
    data = serializers.JSONField()


class JobMonitorRowSerializer(serializers.Serializer):
    username = serializers.CharField(required=False, allow_blank=True)
    jobs = serializers.IntegerField(required=False)
    node_hours = serializers.FloatField(required=False)
    avg_wait = serializers.FloatField(required=False, allow_null=True)


class JobMonitorResponseSerializer(serializers.Serializer):
    data = JobMonitorRowSerializer(many=True)


class JobMonitorGpuRowSerializer(serializers.Serializer):
    username = serializers.CharField(required=False, allow_blank=True)
    gpu_jobs = serializers.IntegerField(required=False)
    gpu_node_hours = serializers.FloatField(required=False)


class JobMonitorGpuResponseSerializer(serializers.Serializer):
    data = JobMonitorGpuRowSerializer(many=True)


class SacctIngestResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField(required=False)
    message = serializers.CharField(required=False)
    detail = serializers.CharField(required=False)


class PublicClusterMetricBlockSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    bokeh_histogram_json_item = serializers.JSONField(required=False, allow_null=True)


class PublicClusterDashboardSerializer(serializers.Serializer):
    status = serializers.CharField(required=False)
    machine_name = serializers.CharField(required=False)
    expansion_factors = serializers.DictField(required=False)
    monthly_metrics = PublicClusterMetricBlockSerializer(many=True, required=False)
