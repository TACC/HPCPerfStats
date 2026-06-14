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
    """Job list performance column from job_list_performance.summarize_performance."""

    label = serializers.CharField(required=False)
    tone = serializers.CharField(required=False)
    aria_label = serializers.CharField(required=False)
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
    account = serializers.CharField(required=False, allow_null=True)
    sample_count = serializers.IntegerField(required=False)
    queue = serializers.CharField(required=False, allow_null=True)
    state = serializers.CharField(required=False, allow_null=True)
    QOS = serializers.CharField(required=False, allow_null=True)
    jobname = serializers.CharField(required=False, allow_null=True)
    host_list = serializers.ListField(child=serializers.CharField(), required=False)
    performance = JobPerformanceSerializer(required=False)
    color = serializers.CharField(required=False)


class JobListAggregatesSerializer(serializers.Serializer):
    total_node_hours = serializers.FloatField(required=False)
    queue_wait_mean_hours = serializers.FloatField(required=False)


class JobListPaginationSerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False)
    num_pages = serializers.IntegerField(required=False)
    has_previous = serializers.BooleanField(required=False)
    has_next = serializers.BooleanField(required=False)
    previous_page_number = serializers.IntegerField(required=False, allow_null=True)
    next_page_number = serializers.IntegerField(required=False, allow_null=True)


class JobListHistogramEnvelopeSerializer(serializers.Serializer):
    """Histogram metadata without embedded Bokeh plot_item payloads."""

    title = serializers.CharField(required=False, allow_blank=True)
    metric = serializers.CharField(required=False, allow_blank=True)
    queue = serializers.CharField(required=False, allow_blank=True)
    unavailable_reason = serializers.CharField(required=False, allow_null=True)


class JobListResponseSerializer(serializers.Serializer):
    nj = serializers.IntegerField(required=False)
    job_list = JobListEntrySerializer(many=True, required=False)
    filter_summary = serializers.ListField(child=serializers.CharField(), required=False)
    histograms = serializers.DictField(child=JobListHistogramEnvelopeSerializer(), required=False)
    aggregates = JobListAggregatesSerializer(required=False)
    pagination = JobListPaginationSerializer(required=False)
    qname = serializers.CharField(required=False)
    order_by = serializers.CharField(required=False)
    current_path = serializers.CharField(required=False, allow_null=True)


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
    proc_list = serializers.ListField(child=serializers.CharField(), required=False)
    derived_data_status = serializers.CharField(required=False)
    client_url = serializers.CharField(required=False, allow_null=True)
    server_url = serializers.CharField(required=False, allow_null=True)
    gpu_active = serializers.IntegerField(required=False, allow_null=True)
    gpu_utilization_max = serializers.FloatField(required=False, allow_null=True)
    gpu_utilization_mean = serializers.FloatField(required=False, allow_null=True)
    gpu_count = serializers.IntegerField(required=False, allow_null=True)
    multiprecision_cpu_plot_item = serializers.JSONField(required=False, allow_null=True)
    multiprecision_cpu_unavailable_reason = serializers.CharField(
        required=False, allow_null=True
    )
    multiprecision_gpu_plot_item = serializers.JSONField(required=False, allow_null=True)
    multiprecision_gpu_unavailable_reason = serializers.CharField(
        required=False, allow_null=True
    )
    staff_metrics_distinct_time_count = serializers.IntegerField(required=False)


class JobPlotsResponseSerializer(serializers.Serializer):
    """Legacy plot keys returned by job_plots (summary, roofline, GPU roofline)."""

    mscript = serializers.CharField(required=False, allow_blank=True)
    mdiv = serializers.CharField(required=False, allow_blank=True)
    mplot_item = serializers.JSONField(required=False, allow_null=True)
    mplot_unavailable_reason = serializers.CharField(required=False, allow_null=True)
    rscript = serializers.CharField(required=False, allow_blank=True)
    rdiv = serializers.CharField(required=False, allow_blank=True)
    rplot_item = serializers.JSONField(required=False, allow_null=True)
    rplot_unavailable_reason = serializers.CharField(required=False, allow_null=True)
    grscript = serializers.CharField(required=False, allow_blank=True)
    grdiv = serializers.CharField(required=False, allow_blank=True)
    grplot_item = serializers.JSONField(required=False, allow_null=True)
    grplot_unavailable_reason = serializers.CharField(required=False, allow_null=True)
    status = serializers.CharField(required=False)
    detail = serializers.CharField(required=False)
    retry_after_seconds = serializers.IntegerField(required=False)
    loading_plots = serializers.ListField(child=serializers.CharField(), required=False)
    progressive = serializers.BooleanField(required=False)
    plot = serializers.CharField(required=False)
    plot_item = serializers.JSONField(required=False, allow_null=True)
    unavailable_reason = serializers.CharField(required=False, allow_null=True)


class JobListHistogramResponseSerializer(serializers.Serializer):
    """Per-metric histogram envelope from job_list_histograms."""

    group = serializers.CharField(required=False)
    metric = serializers.CharField(required=False, allow_null=True)
    nj = serializers.IntegerField(required=False)
    title = serializers.CharField(required=False, allow_blank=True)
    plot_item_thumb = serializers.JSONField(required=False, allow_null=True)
    plot_item_full = serializers.JSONField(required=False, allow_null=True)
    plot_unavailable_reason = serializers.CharField(required=False, allow_null=True)


class TypeDetailResponseSerializer(serializers.Serializer):
    type_name = serializers.CharField(required=False)
    jobid = serializers.CharField(required=False)
    tplot_item = serializers.JSONField(required=False, allow_null=True)
    tplot_unavailable_reason = serializers.CharField(required=False, allow_null=True)
    stats_data = serializers.ListField(child=serializers.JSONField(), required=False)
    schema = serializers.ListField(child=serializers.JSONField(), required=False)
    status = serializers.CharField(required=False)


class HostPlotResponseSerializer(serializers.Serializer):
    host = serializers.CharField()
    plot_item = serializers.JSONField(allow_null=True)
    plot_unavailable_reason = serializers.CharField(allow_null=True, required=False)
    end_time__gte = serializers.CharField()
    end_time__lte = serializers.CharField()


class AdminMonitorHostStatSerializer(serializers.Serializer):
    host = serializers.CharField(required=False, allow_blank=True)
    last_time = serializers.CharField(required=False, allow_null=True)
    age_bucket = serializers.CharField(required=False, allow_blank=True)


class AdminMonitorResponseSerializer(serializers.Serializer):
    """Section keys at top level; only one or a combined bundle is present per request."""

    host_stats = AdminMonitorHostStatSerializer(many=True, required=False)
    rabbitmq_host_stats = AdminMonitorHostStatSerializer(many=True, required=False)
    cache_stats = serializers.DictField(required=False)
    rabbitmq_stats = serializers.DictField(required=False)
    timescaledb_stats = serializers.DictField(required=False)
    xalt_stats = serializers.DictField(required=False)


class JobMonitorRowSerializer(serializers.Serializer):
    username = serializers.CharField(required=False, allow_blank=True)
    total_jobs = serializers.IntegerField(required=False)
    failed_jobs = serializers.IntegerField(required=False)
    failed_rate = serializers.FloatField(required=False)
    timedout_jobs = serializers.IntegerField(required=False)
    timedout_rate = serializers.FloatField(required=False)


class JobMonitorResponseSerializer(serializers.Serializer):
    window_days = serializers.IntegerField(required=False)
    start_time = serializers.CharField(required=False)
    end_time = serializers.CharField(required=False)
    results = JobMonitorRowSerializer(many=True, required=False)


class JobMonitorGpuResponseSerializer(serializers.Serializer):
    username = serializers.CharField(required=False, allow_blank=True)
    gpu_count_total = serializers.IntegerField(required=False, allow_null=True)
    gpu_active_total = serializers.IntegerField(required=False, allow_null=True)
    gpu_active_percentage = serializers.FloatField(required=False, allow_null=True)
    has_data = serializers.BooleanField(required=False)


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
