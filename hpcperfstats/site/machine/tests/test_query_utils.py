"""Unit tests for query_utils date normalization and month expansion."""
import pytest

from hpcperfstats.site.machine.query_utils import (
    expand_month_date_to_range,
    normalize_date_param,
    normalize_job_list_query_params,
)


class TestNormalizeDateParam:
    """normalize_date_param converts shorthand to YYYY-MM-DD."""

    def test_shorthand_month_to_first_day(self):
        assert normalize_date_param("2026-1") == "2026-01-01"
        assert normalize_date_param("2026-12") == "2026-12-01"

    def test_shorthand_month_day(self):
        assert normalize_date_param("2026-1-5") == "2026-01-05"
        assert normalize_date_param("2026-12-3") == "2026-12-03"

    def test_full_date_unchanged(self):
        assert normalize_date_param("2026-01-05") == "2026-01-05"
        assert normalize_date_param("2026-01-05T00:00:00") == "2026-01-05"

    def test_non_date_unchanged(self):
        assert normalize_date_param("queue_name") == "queue_name"
        assert normalize_date_param("") == ""
        assert normalize_date_param(None) is None


class TestNormalizeJobListQueryParams:
    """normalize_job_list_query_params only normalizes time-related keys."""

    def test_normalizes_time_params(self):
        out = normalize_job_list_query_params({"end_time__date": "2026-1"})
        assert out["end_time__date"] == "2026-01-01"

    def test_preserves_month_only_end_time_date(self):
        """Month-only YYYY-MM is preserved so expand_month_date_to_range can expand to full month."""
        out = normalize_job_list_query_params({"end_time__date": "2026-01"})
        assert out["end_time__date"] == "2026-01"
        # Full pipeline: normalize (preserve) then expand
        expanded = expand_month_date_to_range(out)
        assert expanded["end_time__date__gte"] == "2026-01-01"
        assert expanded["end_time__date__lte"] == "2026-01-31"

    def test_preserves_year_only_end_time_date(self):
        """Year-only YYYY is preserved so expand_month_date_to_range can expand to full year."""
        out = normalize_job_list_query_params({"end_time__date": "2024"})
        assert out["end_time__date"] == "2024"
        expanded = expand_month_date_to_range(out)
        assert expanded["end_time__date__gte"] == "2024-01-01"
        assert expanded["end_time__date__lte"] == "2024-12-31"

    def test_leaves_other_params_unchanged(self):
        out = normalize_job_list_query_params({"queue": "normal", "page": "1"})
        assert out["queue"] == "normal"
        assert out["page"] == "1"


class TestExpandMonthDateToRange:
    """expand_month_date_to_range expands YYYY-MM to gte/lte for that month."""

    def test_expands_month_only(self):
        out = expand_month_date_to_range({"end_time__date": "2026-01"})
        assert "end_time__date" not in out
        assert out["end_time__date__gte"] == "2026-01-01"
        assert out["end_time__date__lte"] == "2026-01-31"

    def test_expands_february(self):
        out = expand_month_date_to_range({"end_time__date": "2024-02"})
        assert out["end_time__date__lte"] == "2024-02-29"

    def test_full_date_not_expanded(self):
        out = expand_month_date_to_range({"end_time__date": "2026-01-15"})
        assert out["end_time__date"] == "2026-01-15"
        assert "end_time__date__gte" not in out

    def test_expands_year_only(self):
        """Year-only YYYY is expanded to full year range."""
        out = expand_month_date_to_range({"end_time__date": "2024"})
        assert "end_time__date" not in out
        assert out["end_time__date__gte"] == "2024-01-01"
        assert out["end_time__date__lte"] == "2024-12-31"

    def test_no_end_time_date_unchanged(self):
        out = expand_month_date_to_range({"queue": "x"})
        assert out == {"queue": "x"}
