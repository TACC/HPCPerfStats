"""Tests for job list qname and filter_summary helpers."""

from hpcperfstats.site.machine.job_list_filter_summary import (
    build_job_list_qname_and_filter_summary,
)


def test_filtered_jobs_qname_and_summary():
    fields = {
        "runtime__gte": "3600",
        "queue": "normal",
        "end_time__gte": "2024-01-01",
    }
    qname, summary = build_job_list_qname_and_filter_summary(fields)
    assert qname == "Jobs in queue normal"
    assert any("Runtime" in line for line in summary)
    assert any("2024-01-01" in line for line in summary)


def test_generic_jobs_when_no_filters():
    qname, summary = build_job_list_qname_and_filter_summary({})
    assert qname == "Jobs"
    assert summary == []


def test_filtered_jobs_title_without_queue():
    qname, summary = build_job_list_qname_and_filter_summary({"runtime__gte": "1"})
    assert qname == "Filtered jobs"
    assert summary
