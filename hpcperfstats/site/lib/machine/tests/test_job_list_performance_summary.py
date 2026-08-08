"""Tests for job list performance classification and ORM annotations."""
import pytest
from django.utils import timezone

from hpcperfstats.site.lib.machine.job_list_performance import (
    LABEL_METRICS_AND_PLOTS_AVAILABLE,
    LABEL_METRICS_AVAILABLE,
    LABEL_NOT_YET_COMPLETED,
    LABEL_TOO_FEW_SAMPLES,
    LABEL_TOO_SHORT,
    MONITORING_GAPS_MIN_DISTINCT_TIMES,
    SHORT_RUNTIME_NO_METRICS_SECONDS,
    annotate_job_list_performance_fields,
    expand_performance_sort_ranks_for_filter,
    performance_status_label,
    summarize_performance,
)
from hpcperfstats.site.lib.machine.models import job_data, metrics_data
from hpcperfstats.site.lib.machine.serializers import JobListSerializer
from hpcperfstats.site.lib.machine.query_utils import parse_job_list_performance_sort_ranks


@pytest.mark.machine_unit_mock
class TestSummarizePerformance:
    """Unit tests for summarize_performance (all sort_rank branches; no DB)."""

    def test_short_runtime_threshold_is_600_seconds(self):
        assert SHORT_RUNTIME_NO_METRICS_SECONDS == 600.0

    def test_rank_0_requires_artifacts_ready(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=3,
            distinct_time_count=1,
            runtime=100.0,
            plots_artifacts_ready=True,
        )
        assert out["sort_rank"] == 0
        assert out["tone"] == "success"
        assert out["label"] == LABEL_METRICS_AND_PLOTS_AVAILABLE
        assert LABEL_METRICS_AND_PLOTS_AVAILABLE in out["aria_label"]

    def test_rank_1_metrics_only_when_artifacts_not_ready(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=3,
            distinct_time_count=1,
            runtime=100.0,
            plots_artifacts_ready=False,
        )
        assert out["sort_rank"] == 1
        assert out["tone"] == "info"
        assert out["label"] == LABEL_METRICS_AVAILABLE

    def test_rank_2_too_few_samples_high_dtc(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=0,
            distinct_time_count=MONITORING_GAPS_MIN_DISTINCT_TIMES,
            runtime=3600.0,
        )
        assert out["sort_rank"] == 2
        assert out["tone"] == "warning"
        assert out["label"] == LABEL_TOO_FEW_SAMPLES

    def test_rank_3_few_distinct_times(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=0,
            distinct_time_count=MONITORING_GAPS_MIN_DISTINCT_TIMES - 1,
            runtime=3600.0,
        )
        assert out["sort_rank"] == 3
        assert out["tone"] == "warning"
        assert out["label"] == LABEL_TOO_FEW_SAMPLES

    def test_rank_4_zero_distinct_times(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=0,
            distinct_time_count=0,
            runtime=3600.0,
        )
        assert out["sort_rank"] == 4
        assert out["label"] == LABEL_TOO_FEW_SAMPLES
        assert out["tone"] == "warning"

    def test_rank_4_null_distinct_times(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=3600.0,
        )
        assert out["sort_rank"] == 4
        assert out["label"] == LABEL_TOO_FEW_SAMPLES

    def test_performance_status_label_ranks_2_3_4_match(self):
        assert performance_status_label(2) == LABEL_TOO_FEW_SAMPLES
        assert performance_status_label(3) == LABEL_TOO_FEW_SAMPLES
        assert performance_status_label(4) == LABEL_TOO_FEW_SAMPLES

    def test_rank_5_no_rows_short_runtime_label(self):
        out = summarize_performance(
            has_metrics_row=False,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=SHORT_RUNTIME_NO_METRICS_SECONDS - 1,
        )
        assert out["sort_rank"] == 5
        assert out["label"] == LABEL_TOO_SHORT

    def test_rank_5_still_applies_just_under_600(self):
        out = summarize_performance(
            has_metrics_row=False,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=599.0,
        )
        assert out["sort_rank"] == 5
        assert out["label"] == LABEL_TOO_SHORT

    def test_rank_6_no_rows_runtime_equals_600(self):
        out = summarize_performance(
            has_metrics_row=False,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=SHORT_RUNTIME_NO_METRICS_SECONDS,
        )
        assert out["sort_rank"] == 6
        assert out["label"] == LABEL_NOT_YET_COMPLETED

    def test_rank_6_no_rows_runtime_above_600(self):
        out = summarize_performance(
            has_metrics_row=False,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=3600.0,
        )
        assert out["sort_rank"] == 6
        assert out["label"] == LABEL_NOT_YET_COMPLETED

    def test_rank_6_no_rows_null_runtime(self):
        out = summarize_performance(
            has_metrics_row=False,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=None,
        )
        assert out["sort_rank"] == 6
        assert out["label"] == LABEL_NOT_YET_COMPLETED

    def test_expand_too_few_filter_ranks(self):
        assert expand_performance_sort_ranks_for_filter([2]) == [2, 3, 4]
        assert expand_performance_sort_ranks_for_filter([0, 3]) == [0, 2, 3, 4]

    def test_parse_ranks_accepts_0_through_6(self):
        assert parse_job_list_performance_sort_ranks("0,6") == [0, 6]
        assert parse_job_list_performance_sort_ranks("7") == []


@pytest.mark.django_db
class TestAnnotateJobListPerformanceFields:
    """ORM annotation matches summarize_performance for representative rows."""

    @staticmethod
    def _create_job(jid, *, dtc=None, runtime=3600.0):
        now = timezone.now()
        return job_data.objects.create(
            jid=jid,
            submit_time=now,
            start_time=now,
            end_time=now,
            runtime=runtime,
            username="u",
            host_list=["h1"],
            metrics_distinct_time_count=dtc,
        )

    def test_annotation_matches_classifier_for_each_rank(self):
        # Non-PG: plots_artifacts_ready=False → value jobs are rank 1.
        j0 = self._create_job("perf0", dtc=10)
        metrics_data.objects.create(
            jid=j0, type="t", metric="m", units="u", value=1.0
        )

        j1 = self._create_job("perf1", dtc=MONITORING_GAPS_MIN_DISTINCT_TIMES)
        metrics_data.objects.create(jid=j1, type="t", metric="m1", units="u", value=None)

        j2 = self._create_job("perf2", dtc=2)
        metrics_data.objects.create(jid=j2, type="t", metric="m2", units="u", value=None)

        j3 = self._create_job("perf3", dtc=0)
        metrics_data.objects.create(jid=j3, type="t", metric="m3", units="u", value=None)

        self._create_job("perf4", dtc=None, runtime=SHORT_RUNTIME_NO_METRICS_SECONDS)

        qs = job_data.objects.filter(
            jid__in=["perf0", "perf1", "perf2", "perf3", "perf4"]
        )
        qs = annotate_job_list_performance_fields(qs).order_by("jid")
        rows = {j.jid: j for j in qs}

        for jid, job in rows.items():
            summary = summarize_performance(
                has_metrics_row=job.has_metrics_data,
                metrics_value_count=job.metrics_value_count,
                distinct_time_count=job.metrics_distinct_time_count,
                runtime=job.runtime,
                plots_artifacts_ready=bool(job.plots_artifacts_ready),
            )
            assert job.performance_sort_rank == summary["sort_rank"], jid

        assert rows["perf0"].performance_sort_rank == 1
        assert rows["perf1"].performance_sort_rank == 2
        assert rows["perf2"].performance_sort_rank == 3
        assert rows["perf3"].performance_sort_rank == 4
        assert rows["perf4"].performance_sort_rank == 6

    def test_order_by_performance_sort_rank_ascending(self):
        j0 = self._create_job("ord0", dtc=10)
        metrics_data.objects.create(jid=j0, type="t", metric="m", units="u", value=1.0)
        self._create_job("ord6", dtc=None, runtime=SHORT_RUNTIME_NO_METRICS_SECONDS)
        j2 = self._create_job("ord2", dtc=MONITORING_GAPS_MIN_DISTINCT_TIMES)
        metrics_data.objects.create(jid=j2, type="t", metric="m1", units="u", value=None)

        qs = annotate_job_list_performance_fields(
            job_data.objects.filter(jid__in=["ord0", "ord6", "ord2"])
        ).order_by("performance_sort_group", "jid")
        assert [j.jid for j in qs] == ["ord0", "ord2", "ord6"]

    def test_ranks_2_3_4_share_performance_sort_group(self):
        j2 = self._create_job("grp2", dtc=MONITORING_GAPS_MIN_DISTINCT_TIMES)
        metrics_data.objects.create(jid=j2, type="t", metric="m2", units="u", value=None)
        j3 = self._create_job("grp3", dtc=2)
        metrics_data.objects.create(jid=j3, type="t", metric="m3", units="u", value=None)
        j4 = self._create_job("grp4", dtc=0)
        metrics_data.objects.create(jid=j4, type="t", metric="m4", units="u", value=None)

        qs = annotate_job_list_performance_fields(
            job_data.objects.filter(jid__in=["grp2", "grp3", "grp4"])
        )
        rows = {j.jid: j for j in qs}
        assert rows["grp2"].performance_sort_rank == 2
        assert rows["grp3"].performance_sort_rank == 3
        assert rows["grp4"].performance_sort_rank == 4
        assert rows["grp2"].performance_sort_group == 2
        assert rows["grp3"].performance_sort_group == 2
        assert rows["grp4"].performance_sort_group == 2

    def test_order_by_performance_sort_rank_full_product_sequence(self):
        """Ascending group: metrics-available, too-few bucket, too-short, not-yet."""
        j1 = self._create_job("full1", dtc=1)
        metrics_data.objects.create(jid=j1, type="t", metric="m0", units="u", value=1.0)
        self._create_job("full6", dtc=None, runtime=SHORT_RUNTIME_NO_METRICS_SECONDS)
        j2 = self._create_job("full2", dtc=MONITORING_GAPS_MIN_DISTINCT_TIMES)
        metrics_data.objects.create(jid=j2, type="t", metric="m2", units="u", value=None)
        j3 = self._create_job("full3", dtc=2)
        metrics_data.objects.create(jid=j3, type="t", metric="m3", units="u", value=None)
        j4 = self._create_job("full4", dtc=0)
        metrics_data.objects.create(jid=j4, type="t", metric="m4", units="u", value=None)
        self._create_job("full5", dtc=None, runtime=SHORT_RUNTIME_NO_METRICS_SECONDS - 1)

        qs = annotate_job_list_performance_fields(
            job_data.objects.filter(
                jid__in=["full1", "full2", "full3", "full4", "full5", "full6"]
            )
        ).order_by("performance_sort_group", "jid")
        assert [j.jid for j in qs] == [
            "full1",
            "full2",
            "full3",
            "full4",
            "full5",
            "full6",
        ]


@pytest.mark.django_db
def test_job_list_serializer_exposes_metrics_available_without_artifacts():
    now = timezone.now()
    j = job_data.objects.create(
        jid="srlz1",
        submit_time=now,
        start_time=now,
        end_time=now,
        runtime=200.0,
        username="u",
        host_list=["h1"],
        metrics_distinct_time_count=10,
    )
    metrics_data.objects.create(jid=j, type="t", metric="m", units="u", value=2.0)
    j = annotate_job_list_performance_fields(job_data.objects.filter(jid="srlz1")).first()
    data = JobListSerializer(j).data
    assert "has_metrics" not in data
    # Non-PG annotate fail-closes artifacts → Metrics available (rank 1).
    assert data["performance"]["sort_rank"] == 1
    assert data["performance"]["label"] == LABEL_METRICS_AVAILABLE
    assert data["performance"]["tone"] == "info"


@pytest.mark.django_db
def test_build_job_list_orders_too_few_as_one_bucket():
    """Public order_by=performance_sort_rank uses performance_sort_group + jid."""
    from django.test import RequestFactory

    from hpcperfstats.site.lib.machine.api import _build_job_list_queryset_from_request

    now = timezone.now()

    def _job(jid, *, dtc=None, runtime=3600.0):
        return job_data.objects.create(
            jid=jid,
            submit_time=now,
            start_time=now,
            end_time=now,
            runtime=runtime,
            username="u",
            host_list=["h1"],
            metrics_distinct_time_count=dtc,
        )

    j1 = _job("api1", dtc=1)
    metrics_data.objects.create(jid=j1, type="t", metric="m0", units="u", value=1.0)
    _job("api6", dtc=None, runtime=SHORT_RUNTIME_NO_METRICS_SECONDS)
    j4 = _job("api4", dtc=0)
    metrics_data.objects.create(jid=j4, type="t", metric="m4", units="u", value=None)
    j2 = _job("api2", dtc=MONITORING_GAPS_MIN_DISTINCT_TIMES)
    metrics_data.objects.create(jid=j2, type="t", metric="m2", units="u", value=None)

    factory = RequestFactory()
    request = factory.get(
        "/api/jobs/",
        {"order_by": "performance_sort_rank", "username": "u"},
    )
    request.session = {"username": "admin", "is_staff": True}
    qs, _fields, _cur, order_by = _build_job_list_queryset_from_request(
        request, annotate_all=True
    )
    assert order_by == "performance_sort_rank"
    assert [j.jid for j in qs] == ["api1", "api2", "api4", "api6"]
