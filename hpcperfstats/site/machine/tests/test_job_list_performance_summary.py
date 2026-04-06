"""Tests for job list performance classification and ORM annotations."""
import pytest
from django.utils import timezone

from hpcperfstats.site.machine.job_list_performance import (
    MONITORING_GAPS_MIN_DISTINCT_TIMES,
    SHORT_RUNTIME_NO_METRICS_SECONDS,
    annotate_job_list_performance_fields,
    summarize_performance,
)
from hpcperfstats.site.machine.models import job_data, metrics_data
from hpcperfstats.site.machine.serializers import JobListSerializer


class TestSummarizePerformance:
    """Unit tests for summarize_performance (all sort_rank branches)."""

    def test_rank_0_when_values_present(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=3,
            distinct_time_count=1,
            runtime=100.0,
        )
        assert out["sort_rank"] == 0
        assert out["tone"] == "success"
        assert out["label"] == "Summary available"
        assert "Summary available" in out["aria_label"]

    def test_rank_1_monitoring_gaps(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=0,
            distinct_time_count=MONITORING_GAPS_MIN_DISTINCT_TIMES,
            runtime=3600.0,
        )
        assert out["sort_rank"] == 1
        assert out["tone"] == "info"
        assert out["label"] == "Monitoring gaps"

    def test_rank_2_few_distinct_times(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=0,
            distinct_time_count=MONITORING_GAPS_MIN_DISTINCT_TIMES - 1,
            runtime=3600.0,
        )
        assert out["sort_rank"] == 2
        assert out["tone"] == "warning"

    def test_rank_3_zero_distinct_times(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=0,
            distinct_time_count=0,
            runtime=3600.0,
        )
        assert out["sort_rank"] == 3

    def test_rank_3_null_distinct_times(self):
        out = summarize_performance(
            has_metrics_row=True,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=3600.0,
        )
        assert out["sort_rank"] == 3
        assert out["label"] == "Not enough samples to summarize"

    def test_rank_4_no_rows_short_runtime_label(self):
        out = summarize_performance(
            has_metrics_row=False,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=SHORT_RUNTIME_NO_METRICS_SECONDS - 1,
        )
        assert out["sort_rank"] == 4
        assert out["label"] == "Too short to measure"

    def test_rank_4_no_rows_default_label(self):
        out = summarize_performance(
            has_metrics_row=False,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=SHORT_RUNTIME_NO_METRICS_SECONDS,
        )
        assert out["sort_rank"] == 4
        assert out["label"] == "Not summarized yet"

    def test_rank_4_no_rows_null_runtime(self):
        out = summarize_performance(
            has_metrics_row=False,
            metrics_value_count=0,
            distinct_time_count=None,
            runtime=None,
        )
        assert out["sort_rank"] == 4
        assert out["label"] == "Not summarized yet"


@pytest.mark.django_db
class TestAnnotateJobListPerformanceFields:
    """ORM annotation matches summarize_performance for representative rows."""

    @staticmethod
    def _create_job(jid, *, dtc=None, runtime=500.0):
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

        j4 = self._create_job("perf4", dtc=None, runtime=300.0)

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
            )
            assert job.performance_sort_rank == summary["sort_rank"], jid

    def test_order_by_performance_sort_rank_ascending(self):
        j0 = self._create_job("ord0", dtc=10)
        metrics_data.objects.create(jid=j0, type="t", metric="m", units="u", value=1.0)
        j4a = self._create_job("ord4a", dtc=None, runtime=500.0)
        j1 = self._create_job("ord1", dtc=MONITORING_GAPS_MIN_DISTINCT_TIMES)
        metrics_data.objects.create(jid=j1, type="t", metric="m1", units="u", value=None)

        qs = annotate_job_list_performance_fields(
            job_data.objects.filter(jid__in=["ord0", "ord4a", "ord1"])
        ).order_by("performance_sort_rank", "jid")
        assert [j.jid for j in qs] == ["ord0", "ord1", "ord4a"]

    def test_order_by_performance_sort_rank_descending(self):
        j0 = self._create_job("des0", dtc=10)
        metrics_data.objects.create(jid=j0, type="t", metric="m", units="u", value=1.0)
        j4a = self._create_job("des4a", dtc=None, runtime=500.0)

        qs = annotate_job_list_performance_fields(
            job_data.objects.filter(jid__in=["des0", "des4a"])
        ).order_by("-performance_sort_rank", "jid")
        assert [j.jid for j in qs] == ["des4a", "des0"]


@pytest.mark.django_db
def test_job_list_serializer_exposes_performance_not_has_metrics():
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
    assert data["performance"]["sort_rank"] == 0
    assert data["performance"]["label"] == "Summary available"
    assert data["performance"]["tone"] == "success"
