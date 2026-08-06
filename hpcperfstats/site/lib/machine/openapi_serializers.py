"""
OpenAPI schema serializers for drf-spectacular (SPA-facing machine API).
"""
from __future__ import annotations

from rest_framework import serializers


class ErrorDetailSerializer(serializers.Serializer):
    """
    Hold ErrorDetailSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    detail = serializers.CharField(required=False)
    error = serializers.CharField(required=False)
    login_url = serializers.CharField(required=False)


class BokehJsonItemField(serializers.JSONField):
    """
    Opaque Bokeh ``json_item`` document for ``Bokeh.embed.embed_item``.
    
    Must stay free-form JSON so Orval Zod does not strip ``type`` /
      ``attributes``
    from ``doc.roots[]``. SPA embed-time validation is ``parseBokehJsonItem``.
    Do not replace with a nested Serializer that only models ``{id}`` roots.
    """


class SessionInfoSerializer(serializers.Serializer):
    """
    Hold SessionInfoSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    logged_in = serializers.BooleanField()
    username = serializers.CharField()
    is_staff = serializers.BooleanField()
    # Optional in schema: SPA uses build-time site-identity.ts; field still sent for compatibility.
    machine_name = serializers.CharField(required=False)


class UserApiKeySerializer(serializers.Serializer):
    """
    Hold UserApiKeySerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    username = serializers.CharField()
    raw_key = serializers.CharField(allow_null=True, required=False)
    key_prefix = serializers.CharField()


class DropStaffResponseSerializer(serializers.Serializer):
    """
    Hold DropStaffResponseSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    ok = serializers.BooleanField()
    message = serializers.CharField()
    is_staff = serializers.BooleanField()


class InvalidateCacheRequestSerializer(serializers.Serializer):
    """
    Hold InvalidateCacheRequestSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    page_path = serializers.CharField()


class InvalidateCacheResponseSerializer(serializers.Serializer):
    """
    Hold InvalidateCacheResponseSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    ok = serializers.BooleanField()
    page_path = serializers.CharField()
    deleted_keys = serializers.IntegerField()
    scanned_keys = serializers.IntegerField(required=False)
    matched_sample = serializers.ListField(child=serializers.CharField(), required=False)
    truncated_scan = serializers.BooleanField(required=False)


class HomeMetricOptionSerializer(serializers.Serializer):
    """
    Hold HomeMetricOptionSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    type = serializers.CharField()
    metric = serializers.CharField()
    units = serializers.CharField()


class HomeDateDayPairSerializer(serializers.Serializer):
    """
    One [iso_date, day_of_month] pair from home date_list.
    """

    date = serializers.CharField()
    day = serializers.CharField()


class HomeDateMonthEntrySerializer(serializers.Serializer):
    """
    One month bucket: month key plus day pairs (serialized from API tuple).
    """

    month = serializers.CharField()
    days = HomeDateDayPairSerializer(many=True)


class HomeOptionsSerializer(serializers.Serializer):
    """
    Hold HomeOptionsSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
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
    """
    Job list performance column from job_list_performance.summarize_performance.
    """

    label = serializers.CharField(required=False)
    tone = serializers.CharField(required=False)
    aria_label = serializers.CharField(required=False)
    sort_rank = serializers.IntegerField(required=False)


class JobListEntrySerializer(serializers.Serializer):
    """
    Hold JobListEntrySerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
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
    sample_count = serializers.IntegerField(required=False, allow_null=True)
    queue = serializers.CharField(required=False, allow_null=True)
    state = serializers.CharField(required=False, allow_null=True)
    QOS = serializers.CharField(required=False, allow_null=True)
    jobname = serializers.CharField(required=False, allow_null=True)
    host_list = serializers.ListField(child=serializers.CharField(), required=False)
    performance = JobPerformanceSerializer(required=False)
    color = serializers.CharField(required=False)


class JobListAggregatesSerializer(serializers.Serializer):
    """
    Hold JobListAggregatesSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    total_node_hours = serializers.FloatField(required=False)
    queue_wait_mean_hours = serializers.FloatField(required=False)


class JobListPaginationSerializer(serializers.Serializer):
    """
    Hold JobListPaginationSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    page = serializers.IntegerField(required=False)
    num_pages = serializers.IntegerField(required=False)
    has_previous = serializers.BooleanField(required=False)
    has_next = serializers.BooleanField(required=False)
    previous_page_number = serializers.IntegerField(required=False, allow_null=True)
    next_page_number = serializers.IntegerField(required=False, allow_null=True)


class JobListPerformanceStatusOptionSerializer(serializers.Serializer):
    """
    Hold JobListPerformanceStatusOptionSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    sort_rank = serializers.IntegerField()
    label = serializers.CharField()


class JobListFilterOptionsTruncatedSerializer(serializers.Serializer):
    """
    Hold JobListFilterOptionsTruncatedSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    usernames = serializers.BooleanField(required=False)
    accounts = serializers.BooleanField(required=False)
    queues = serializers.BooleanField(required=False)
    states = serializers.BooleanField(required=False)


class JobListFilterOptionsSerializer(serializers.Serializer):
    """
    Hold JobListFilterOptionsSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    usernames = serializers.ListField(child=serializers.CharField(), required=False)
    accounts = serializers.ListField(child=serializers.CharField(), required=False)
    queues = serializers.ListField(child=serializers.CharField(), required=False)
    states = serializers.ListField(child=serializers.CharField(), required=False)
    performance_statuses = JobListPerformanceStatusOptionSerializer(many=True, required=False)
    truncated = JobListFilterOptionsTruncatedSerializer(required=False)


class JobListFilterOptionsResponseSerializer(serializers.Serializer):
    """
    Hold JobListFilterOptionsResponseSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    filter_options = JobListFilterOptionsSerializer(required=False, allow_null=True)


class JobListHistogramEnvelopeSerializer(serializers.Serializer):
    """
    Histogram metadata without embedded Bokeh plot_item payloads.
    """

    title = serializers.CharField(required=False, allow_blank=True)
    metric = serializers.CharField(required=False, allow_blank=True)
    queue = serializers.CharField(required=False, allow_blank=True)
    unavailable_reason = serializers.CharField(required=False, allow_null=True)


class JobListResponseSerializer(serializers.Serializer):
    """
    Hold JobListResponseSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    nj = serializers.IntegerField(required=False)
    job_list = JobListEntrySerializer(many=True, required=False)
    filter_summary = serializers.ListField(child=serializers.CharField(), required=False)
    filter_options = JobListFilterOptionsSerializer(required=False, allow_null=True)
    histograms = serializers.DictField(child=JobListHistogramEnvelopeSerializer(), required=False)
    aggregates = JobListAggregatesSerializer(required=False)
    pagination = JobListPaginationSerializer(required=False)
    qname = serializers.CharField(required=False)
    order_by = serializers.CharField(required=False)
    current_path = serializers.CharField(required=False, allow_null=True)


class JobDetailJobSerializer(JobListEntrySerializer):
    """
    Job detail primary job_data row (extends list entry fields).
    """

    derived_data_status = serializers.CharField(required=False)
    client_url = serializers.CharField(required=False, allow_null=True)
    server_url = serializers.CharField(required=False, allow_null=True)


class JobMetricRowSerializer(serializers.Serializer):
    """
    One row from build_job_metrics_display_list (metrics_data catalog + value).
    """

    type = serializers.CharField(required=False, allow_blank=True)
    metric = serializers.CharField(required=False, allow_blank=True)
    units = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    value = serializers.FloatField(required=False, allow_null=True)
    no_data_reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class ProcListEntrySerializer(serializers.Serializer):
    """
    One proc_data row returned in job_detail proc_list (host_proc KEYS).
    """

    host = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    proc = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    device = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    uid = serializers.IntegerField(required=False, allow_null=True)
    vm_peak = serializers.IntegerField(required=False, allow_null=True)
    vm_size = serializers.IntegerField(required=False, allow_null=True)
    vm_lck = serializers.IntegerField(required=False, allow_null=True)
    vm_hwm = serializers.IntegerField(required=False, allow_null=True)
    vm_rss = serializers.IntegerField(required=False, allow_null=True)
    vm_data = serializers.IntegerField(required=False, allow_null=True)
    vm_stk = serializers.IntegerField(required=False, allow_null=True)
    vm_exe = serializers.IntegerField(required=False, allow_null=True)
    vm_lib = serializers.IntegerField(required=False, allow_null=True)
    vm_pte = serializers.IntegerField(required=False, allow_null=True)
    vm_swap = serializers.IntegerField(required=False, allow_null=True)
    threads = serializers.IntegerField(required=False, allow_null=True)


class StaffArtifactContractSerializer(serializers.Serializer):
    """
    Staff Job Detail: runtime vs stored plot/detail artifact schema versions.
    """

    current_plot = serializers.IntegerField()
    current_detail = serializers.IntegerField()
    db_plot = serializers.ListField(child=serializers.IntegerField())
    db_detail = serializers.ListField(child=serializers.IntegerField())


class GpuInventoryEntrySerializer(serializers.Serializer):
    """
    Per-(host,dev) GPU util/power for Job Detail Resources inventory.
    """

    host = serializers.CharField()
    dev = serializers.CharField()
    type = serializers.CharField(required=False, allow_blank=True)
    util_max = serializers.FloatField(required=False, allow_null=True)
    util_mean = serializers.FloatField(required=False, allow_null=True)
    power_max_w = serializers.FloatField(required=False, allow_null=True)
    sample_count = serializers.IntegerField(required=False, allow_null=True)


class JobDetailResponseSerializer(serializers.Serializer):
    """
    Hold JobDetailResponseSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    job_data = JobDetailJobSerializer(required=False)
    host_list = serializers.ListField(child=serializers.CharField(), required=False)
    metrics_list = serializers.ListField(
        child=JobMetricRowSerializer(),
        required=False,
    )
    metrics = serializers.JSONField(required=False)
    gpu = serializers.JSONField(required=False)
    fsio = serializers.JSONField(required=False)
    xalt_data = serializers.DictField(required=False)
    schema = serializers.DictField(required=False)
    proc_list = serializers.ListField(child=ProcListEntrySerializer(), required=False)
    derived_data_status = serializers.CharField(required=False)
    client_url = serializers.CharField(required=False, allow_null=True)
    server_url = serializers.CharField(required=False, allow_null=True)
    gpu_active = serializers.IntegerField(required=False, allow_null=True)
    gpu_utilization_max = serializers.FloatField(required=False, allow_null=True)
    gpu_utilization_mean = serializers.FloatField(required=False, allow_null=True)
    gpu_count = serializers.IntegerField(required=False, allow_null=True)
    gpu_inventory = serializers.ListField(
        child=GpuInventoryEntrySerializer(),
        required=False,
    )
    multiprecision_cpu_plot_item = BokehJsonItemField(required=False, allow_null=True)
    multiprecision_cpu_unavailable_reason = serializers.CharField(
        required=False, allow_null=True
    )
    multiprecision_gpu_plot_item = BokehJsonItemField(required=False, allow_null=True)
    multiprecision_gpu_unavailable_reason = serializers.CharField(
        required=False, allow_null=True
    )
    staff_metrics_distinct_time_count = serializers.IntegerField(
        required=False, allow_null=True
    )
    staff_artifact_contract = StaffArtifactContractSerializer(required=False)


class JobPlotsResponseSerializer(serializers.Serializer):
    """
    Legacy plot keys returned by job_plots (summary, roofline, GPU roofline).
    """

    mscript = serializers.CharField(required=False, allow_blank=True)
    mdiv = serializers.CharField(required=False, allow_blank=True)
    mplot_item = BokehJsonItemField(required=False, allow_null=True)
    mplot_unavailable_reason = serializers.CharField(required=False, allow_null=True)
    rscript = serializers.CharField(required=False, allow_blank=True)
    rdiv = serializers.CharField(required=False, allow_blank=True)
    rplot_item = BokehJsonItemField(required=False, allow_null=True)
    rplot_unavailable_reason = serializers.CharField(required=False, allow_null=True)
    grscript = serializers.CharField(required=False, allow_blank=True)
    grdiv = serializers.CharField(required=False, allow_blank=True)
    grplot_item = BokehJsonItemField(required=False, allow_null=True)
    grplot_unavailable_reason = serializers.CharField(required=False, allow_null=True)
    status = serializers.CharField(required=False)
    detail = serializers.CharField(required=False)
    retry_after_seconds = serializers.IntegerField(required=False)
    loading_plots = serializers.ListField(child=serializers.CharField(), required=False)
    progressive = serializers.BooleanField(required=False)
    plot = serializers.CharField(required=False)
    plot_item = BokehJsonItemField(required=False, allow_null=True)
    unavailable_reason = serializers.CharField(required=False, allow_null=True)


class JobListHistogramResponseSerializer(serializers.Serializer):
    """
    Per-metric histogram envelope from job_list_histograms.
    """

    group = serializers.CharField(required=False)
    metric = serializers.CharField(required=False, allow_null=True)
    nj = serializers.IntegerField(required=False)
    histogram_nj = serializers.IntegerField(required=False)
    histogram_sampled = serializers.BooleanField(required=False)
    title = serializers.CharField(required=False, allow_blank=True)
    plot_item_thumb = BokehJsonItemField(required=False, allow_null=True)
    plot_item_full = BokehJsonItemField(required=False, allow_null=True)
    plot_unavailable_reason = serializers.CharField(required=False, allow_null=True)


class JobListHistogramBatchResponseSerializer(serializers.Serializer):
    """
    Batch histogram envelope from job_list_histograms_batch.
    """

    nj = serializers.IntegerField(required=False)
    histogram_nj = serializers.IntegerField(required=False)
    histogram_sampled = serializers.BooleanField(required=False)
    histograms = JobListHistogramResponseSerializer(many=True, required=False)


class TypeDetailResponseSerializer(serializers.Serializer):
    """
    Hold TypeDetailResponseSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    type_name = serializers.CharField(required=False)
    jobid = serializers.CharField(required=False)
    tplot_item = BokehJsonItemField(required=False, allow_null=True)
    tplot_unavailable_reason = serializers.CharField(required=False, allow_null=True)
    stats_data = serializers.ListField(child=serializers.JSONField(), required=False)
    schema = serializers.ListField(child=serializers.JSONField(), required=False)
    status = serializers.CharField(required=False)


class HostPlotResponseSerializer(serializers.Serializer):
    """
    Hold HostPlotResponseSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    host = serializers.CharField()
    plot_item = BokehJsonItemField(allow_null=True)
    plot_unavailable_reason = serializers.CharField(allow_null=True, required=False)
    end_time__gte = serializers.CharField()
    end_time__lte = serializers.CharField()


class AdminMonitorHostStatSerializer(serializers.Serializer):
    """
    Hold AdminMonitorHostStatSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    host = serializers.CharField(required=False, allow_blank=True)
    last_time = serializers.CharField(required=False, allow_null=True)
    age_bucket = serializers.CharField(required=False, allow_blank=True)


class AdminMonitorResponseSerializer(serializers.Serializer):
    """
    Section keys at top level; only one or a combined bundle is present per.
    """

    host_stats = AdminMonitorHostStatSerializer(many=True, required=False)
    rabbitmq_host_stats = AdminMonitorHostStatSerializer(many=True, required=False)
    cache_stats = serializers.DictField(required=False)
    rabbitmq_stats = serializers.DictField(required=False)
    timescaledb_stats = serializers.DictField(required=False)
    xalt_stats = serializers.DictField(required=False)


class JobMonitorRowSerializer(serializers.Serializer):
    """
    Hold JobMonitorRowSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    username = serializers.CharField(required=False, allow_blank=True)
    total_jobs = serializers.IntegerField(required=False)
    failed_jobs = serializers.IntegerField(required=False)
    failed_rate = serializers.FloatField(required=False)
    timedout_jobs = serializers.IntegerField(required=False)
    timedout_rate = serializers.FloatField(required=False)


class JobMonitorResponseSerializer(serializers.Serializer):
    """
    Hold JobMonitorResponseSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    window_days = serializers.IntegerField(required=False)
    start_time = serializers.CharField(required=False)
    end_time = serializers.CharField(required=False)
    results = JobMonitorRowSerializer(many=True, required=False)


class JobMonitorGpuRowSerializer(serializers.Serializer):
    """
    Hold JobMonitorGpuRowSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    username = serializers.CharField(required=False, allow_blank=True)
    gpu_count_total = serializers.IntegerField(required=False, allow_null=True)
    gpu_active_total = serializers.IntegerField(required=False, allow_null=True)
    gpu_active_percentage = serializers.FloatField(required=False, allow_null=True)
    has_data = serializers.BooleanField(required=False)


class JobMonitorGpuResponseSerializer(JobMonitorGpuRowSerializer):
    """
    Single-user GPU rollup or batch wrapper with ``results``.
    """

    results = JobMonitorGpuRowSerializer(many=True, required=False)


class SacctIngestResponseSerializer(serializers.Serializer):
    """
    Hold SacctIngestResponseSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    ok = serializers.BooleanField(required=False)
    message = serializers.CharField(required=False)
    detail = serializers.CharField(required=False)


class PublicClusterMetricBlockSerializer(serializers.Serializer):
    """
    Hold PublicClusterMetricBlockSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    title = serializers.CharField(required=False, allow_blank=True)
    bokeh_histogram_json_item = BokehJsonItemField(required=False, allow_null=True)


class PublicExpansionFactorHistogramBlockSerializer(serializers.Serializer):
    """
    Hold PublicExpansionFactorHistogramBlockSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    scheduler_expansion_factor_daily_means_in_month_count = serializers.IntegerField(
        required=False
    )
    scheduler_expansion_factor_weekly_means_in_year_count = serializers.IntegerField(
        required=False
    )
    histogram_bin_edges = serializers.ListField(required=False)
    histogram_counts = serializers.ListField(required=False)
    expansion_factor_definition = serializers.CharField(required=False, allow_blank=True)
    bokeh_histogram_json_item = BokehJsonItemField(required=False, allow_null=True)


class PublicDashboardExpansionFactorSectionSerializer(serializers.Serializer):
    """
    Hold PublicDashboardExpansionFactorSectionSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    monthly_period_keys = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    yearly_period_keys = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    monthly_daily_histograms = serializers.DictField(required=False)
    yearly_weekly_histograms = serializers.DictField(required=False)


class PublicDashboardSectionsSerializer(serializers.Serializer):
    """
    Hold PublicDashboardSectionsSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    expansion_factor = PublicDashboardExpansionFactorSectionSerializer(required=False)


class PublicClusterDashboardSerializer(serializers.Serializer):
    """
    Hold PublicClusterDashboardSerializer state and behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    
    Subclasses ``Serializer``, extending that type with this class's fields and
    behavior.
    """
    status = serializers.CharField(required=False)
    machine_name = serializers.CharField(required=False)
    detail = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    retry_hint = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    schema_version = serializers.IntegerField(required=False)
    sections = PublicDashboardSectionsSerializer(required=False)
    section = serializers.CharField(required=False, allow_blank=True)
    grouping = serializers.CharField(required=False, allow_blank=True)
    period_key = serializers.CharField(required=False, allow_blank=True)
    block = PublicExpansionFactorHistogramBlockSerializer(required=False)
    expansion_factors = serializers.DictField(required=False)
    monthly_metrics = PublicClusterMetricBlockSerializer(many=True, required=False)
